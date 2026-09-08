"""Unit tests for reconciliation domain entities, normalizer, and data masking."""

from datetime import datetime, timezone
import pytest
from uuid import uuid4

from app.modules.reconciliation.domain.entities import (
    CustomerIdentificationResult,
    CustomerMatchCandidate,
    EvidenceSignal,
    EvidenceType,
    IdentificationStatus,
    SignalStrength,
)
from app.modules.reconciliation.domain.normalizer import PayerStringNormalizer
from app.modules.reconciliation.presentation.schemas import (
    mask_bank_account,
    mask_evidence_matched_value,
    mask_upi_vpa,
)


def test_identification_status_values():
    """Verify all authoritative identification status enum values exist."""
    assert IdentificationStatus.IDENTIFIED == "IDENTIFIED"
    assert IdentificationStatus.AMBIGUOUS == "AMBIGUOUS"
    assert IdentificationStatus.CONFLICTING == "CONFLICTING"
    assert IdentificationStatus.UNKNOWN == "UNKNOWN"
    assert IdentificationStatus.NOT_ELIGIBLE == "NOT_ELIGIBLE"


def test_evidence_signal_immutability_and_serialization():
    """Verify EvidenceSignal is an immutable frozen dataclass and serializes properly."""
    sig = EvidenceSignal(
        evidence_type=EvidenceType.EXACT_BANK_ACCOUNT,
        signal_strength=SignalStrength.STRONG,
        matched_value="1234567890",
        source_field="bank_account_number",
        weight=100.0,
        confidence_delta=100.0,
        metadata={"rail": "IMPS"},
    )
    with pytest.raises(AttributeError):
        sig.weight = 50.0  # type: ignore

    d = sig.to_dict()
    assert d["evidence_type"] == "EXACT_BANK_ACCOUNT"
    assert d["signal_strength"] == "STRONG"
    assert d["matched_value"] == "1234567890"
    assert d["weight"] == 100.0
    assert d["metadata"] == {"rail": "IMPS"}


def test_customer_match_candidate_and_result_immutability():
    """Verify CustomerMatchCandidate and CustomerIdentificationResult immutability."""
    cust_id = uuid4()
    pay_id = uuid4()
    comp_id = uuid4()

    sig = EvidenceSignal(
        evidence_type=EvidenceType.EXACT_ALIAS,
        signal_strength=SignalStrength.STRONG,
        matched_value="Acme Corp",
        source_field="payer_raw_name",
        weight=85.0,
        confidence_delta=85.0,
    )
    cand = CustomerMatchCandidate(
        customer_id=cust_id,
        customer_name="Acme Corporation Ltd",
        composite_score=85.0,
        evidence_signals=[sig],
        rank=1,
    )
    with pytest.raises(AttributeError):
        cand.composite_score = 90.0  # type: ignore

    res = CustomerIdentificationResult(
        payment_id=pay_id,
        company_id=comp_id,
        status=IdentificationStatus.IDENTIFIED,
        primary_candidate=cand,
        candidates=[cand],
        evidence_signals=[sig],
        total_evidence_score=85.0,
        reason_code="DETERMINISTIC_COUNTERPARTY_IDENTIFIED",
        reason_description="Identified via alias",
    )
    with pytest.raises(AttributeError):
        res.status = IdentificationStatus.AMBIGUOUS  # type: ignore

    res_dict = res.to_dict()
    assert res_dict["status"] == "IDENTIFIED"
    assert res_dict["primary_candidate"]["customer_name"] == "Acme Corporation Ltd"
    assert res_dict["is_deterministic"] is True


def test_normalizer_clean_text_unicode_and_control_chars():
    """Verify clean_text normalizes unicode and strips null bytes and bidi overrides."""
    raw = "T\u00e9st \x00 Company\u200e \u202eLtd & Sons@"
    cleaned = PayerStringNormalizer.clean_text(raw)
    assert cleaned == "test company ltd and sons at"

    assert PayerStringNormalizer.clean_text(None) == ""
    assert PayerStringNormalizer.clean_text("") == ""
    assert PayerStringNormalizer.clean_text("   ") == ""


def test_normalizer_legal_suffixes():
    """Verify legal suffixes are stripped while distinct industry nouns are preserved."""
    assert PayerStringNormalizer.normalize_legal_name("Acme Private Limited") == "acme"
    assert PayerStringNormalizer.normalize_legal_name("Acme Pvt Ltd") == "acme"
    assert PayerStringNormalizer.normalize_legal_name("Acme Corp") == "acme"
    assert PayerStringNormalizer.normalize_legal_name("Acme LLC") == "acme"

    # Distinct industry nouns must NOT be stripped or collapsed
    assert PayerStringNormalizer.normalize_legal_name("Acme Industries Pvt Ltd") == "acme industries"
    assert PayerStringNormalizer.normalize_legal_name("Acme Industrials Pvt Ltd") == "acme industrials"
    assert (
        PayerStringNormalizer.normalize_legal_name("Acme Industries Pvt Ltd")
        != PayerStringNormalizer.normalize_legal_name("Acme Industrials Pvt Ltd")
    )


def test_normalizer_token_extraction_and_stopwords():
    """Verify banking keywords, transfer rails, and short numbers are filtered from tokens."""
    narration = "NEFT CR-HDFC000123-ACME SUPPLIES-RTGS PYMT 456789"
    tokens = PayerStringNormalizer.extract_tokens(narration, min_len=3)
    assert "acme" in tokens
    assert "supplies" in tokens
    # Stopwords and rails must be excluded
    assert "neft" not in tokens
    assert "cr" not in tokens
    assert "rtgs" not in tokens
    assert "pymt" not in tokens
    assert "456789" not in tokens


def test_normalizer_identifier_and_vpa_extraction():
    """Verify coordinate normalization and UPI VPA extraction."""
    assert PayerStringNormalizer.normalize_identifier(" 1234-5678 / 90 ") == "1234567890"
    assert PayerStringNormalizer.normalize_identifier("user.name@ICICI") == "user.name@icici"

    text = "Payment received from client.vpa@axisbank on 2026-09-07 ref 987654321012"
    vpas = PayerStringNormalizer.extract_potential_upi_vpas(text)
    assert "client.vpa@axisbank" in vpas

    accounts = PayerStringNormalizer.extract_potential_bank_accounts(text)
    assert "987654321012" in accounts


def test_masking_utilities():
    """Verify banking coordinate and UPI VPA masking."""
    assert mask_bank_account("123456789012") == "********9012"
    assert mask_bank_account("1234") == "****"
    assert mask_bank_account(None) is None

    assert mask_upi_vpa("john.doe@okhdfcbank") == "jo******@okhdfcbank"
    assert mask_upi_vpa("ab@axis") == "**@axis"
    assert mask_upi_vpa(None) is None

    assert mask_evidence_matched_value("123456789012", "EXACT_BANK_ACCOUNT") == "********9012"
    assert mask_evidence_matched_value("user@upi", "EXACT_UPI_VPA") == "us**@upi"
    assert mask_evidence_matched_value("Acme Corp", "EXACT_ALIAS") == "Acme Corp"

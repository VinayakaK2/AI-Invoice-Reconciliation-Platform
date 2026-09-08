"""Adversarial and edge-case integration tests for payment counterparty identification."""

import time
from fastapi.testclient import TestClient
from app.modules.reconciliation.domain.normalizer import PayerStringNormalizer


def test_generic_banking_narration_unrecognized(client: TestClient, registered_owner: dict):
    """Verify generic banking strings and rail codes alone never trigger false match."""
    headers = registered_owner["headers"]

    # Register customer with generic-sounding name components
    client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Standard Online Services Ltd"},
    )

    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "500.00",
            "currency": "INR",
            "narration": "NEFT CR-HDFC0000123-TRANSFER RECEIVED CMS PAYMENT",
            "payer_raw_name": None,
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "UNKNOWN"


def test_empty_and_whitespace_payer_fields(client: TestClient, registered_owner: dict):
    """Verify empty, null, and whitespace-only fields are safely evaluated as UNKNOWN."""
    headers = registered_owner["headers"]

    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "100.00",
            "currency": "INR",
            "narration": "   ",
            "payer_raw_name": "     ",
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "UNKNOWN"


def test_unicode_bidi_override_attack_sanitized(client: TestClient, registered_owner: dict):
    """Verify bidirectional unicode override controls in payer name are sanitized without crashing."""
    headers = registered_owner["headers"]

    client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Safe Clean Company"},
    )

    # Malicious payer name attempting bidi text reversal
    bidi_attack_name = "\u202e\u200eSafe Clean \x00Company\u202c"

    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "1200.00",
            "currency": "INR",
            "narration": "BIDI ATTACK TEST",
            "payer_raw_name": bidi_attack_name,
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Sanitizer strips bidi controls and matches the real company
    assert data["status"] == "IDENTIFIED"
    assert data["primary_candidate"]["customer_name"] == "Safe Clean Company"


def test_formula_injection_characters_sanitized(client: TestClient, registered_owner: dict):
    """Verify formula injection characters in payer name are safely sanitized."""
    headers = registered_owner["headers"]

    client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Formula Security Corp"},
    )

    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "3000.00",
            "currency": "INR",
            "narration": "TXN",
            "payer_raw_name": "=cmd|'/C calc'!A0 Formula Security Corp",
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    # Does not crash or execute, processes as text; formula characters do not yield false identification
    res_data = resp.json()["data"]
    assert res_data["status"] in ("AMBIGUOUS", "UNKNOWN")
    assert res_data["primary_candidate"] is None


def test_redos_long_string_performance(client: TestClient, registered_owner: dict):
    """Verify regex token and VPA extraction completes in milliseconds on adversarial long inputs."""
    headers = registered_owner["headers"]

    # Test HTTP API within field bounds
    adversarial_narration = ("a." * 100) + "@" + ("b-" * 100)

    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "50.00",
            "currency": "INR",
            "narration": adversarial_narration[:450],
            "payer_raw_name": "A" * 200,
        },
    ).json()["data"]["id"]

    t0 = time.perf_counter()
    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    assert elapsed < 0.500

    # Direct unit verification of catastrophic backtracking immunity on 10,000 char strings
    long_narration = ("x." * 2500) + "@" + ("y-" * 2500)
    t1 = time.perf_counter()
    vpas = PayerStringNormalizer.extract_potential_upi_vpas(long_narration)
    tokens = PayerStringNormalizer.extract_tokens(long_narration)
    elapsed_unit = time.perf_counter() - t1

    assert elapsed_unit < 0.100  # under 100ms
    assert isinstance(vpas, list)
    assert isinstance(tokens, set)


def test_archived_customer_not_matched_after_identifier_created(
    client: TestClient, registered_owner: dict
):
    """Verify a customer archived after registration is never matched."""
    headers = registered_owner["headers"]

    cust = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Decommissioned Corp"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "334455667788"},
    )

    # Archive the customer
    arch_resp = client.post(f"/api/v1/customers/{cust}/archive", headers=headers)
    assert arch_resp.status_code == 200

    # Payment arrives for the archived customer's account
    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "PAYMENT FOR ARCHIVED CORP",
            "bank_account_number": "334455667788",
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "UNKNOWN"
    assert resp.json()["data"]["primary_candidate"] is None


def test_false_collapse_prevention_industries_vs_industrials(
    client: TestClient, registered_owner: dict
):
    """Verify distinct trade nouns 'Industries' vs 'Industrials' are not collapsed."""
    headers = registered_owner["headers"]

    client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Premier Industries Ltd"},
    )
    client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Premier Industrials Ltd"},
    )

    # Payment explicitly specifies 'Premier Industries'
    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "15000.00",
            "currency": "INR",
            "narration": "TXN PREMIER INDUSTRIES",
            "payer_raw_name": "Premier Industries Pvt Ltd",
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "IDENTIFIED"
    assert data["primary_candidate"]["customer_name"] == "Premier Industries Ltd"

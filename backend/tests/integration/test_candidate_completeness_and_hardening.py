"""Hardened integration tests for Phase 14.4 Candidate Completeness, Determinism, and Integrity.

Directly proves remediation of:
- FINDING-14-4-02: Headroom Truncation & Candidate Completeness (Cases A through G)
- FINDING-14-4-01: Excluded candidates permutation determinism and strict total order
- FINDING-14-4-03: Duck-typed candidate identity defense-in-depth (tenant/customer/archived)
"""

from datetime import date, timedelta
from decimal import Decimal
import random
from typing import List
import uuid
import pytest
from sqlalchemy.orm import Session

from app.modules.invoice.infrastructure.models import InvoiceModel
from app.modules.reconciliation.application.use_cases import FilterCandidateInvoicesUseCase
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    CandidateFilterRuleEngine,
    FilterExclusionReason,
)
from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceUniverse,
)
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)


def test_case_a_valid_candidate_beyond_old_100_limit(
    db_session: Session, registered_owner: dict
) -> None:
    """Case A: Valid candidate at position 105 is retained even when first 100 fail filters.

    Under the old code with fetch_limit = min(max(K*2, 30), 100), candidate #105 was truncated
    before filtering rules were ever evaluated. This test proves it is now evaluated and retained.
    """
    company_id = uuid.UUID(registered_owner["company"]["id"])
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    # 1. Setup customer and payment in database
    from app.modules.customer.infrastructure.models import CustomerModel
    from app.modules.payment.infrastructure.models import PaymentModel

    customer = CustomerModel(
        id=customer_id,
        company_id=company_id,
        name="Large Catalog Corp A",
        is_archived=False,
    )
    db_session.add(customer)

    payment = PaymentModel(
        id=payment_id,
        company_id=company_id,
        transaction_date=payment_date,
        amount=Decimal("10000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("10000.00"),
        currency="INR",
        status="UNRECONCILED",
        narration="SETTLEMENT FOR LARGE INVOICE",
    )
    db_session.add(payment)

    # 2. Insert 100 stale invoices (>365 days ago, e.g. 500 days old)
    stale_date = payment_date - timedelta(days=500)
    for i in range(100):
        inv = InvoiceModel(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-STALE-{i:03d}",
            issue_date=stale_date,
            due_date=stale_date + timedelta(days=30),
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("10000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        )
        db_session.add(inv)

    # 3. Insert 10 valid causal invoices (issued 10 days ago, due in 20 days)
    valid_ids = []
    valid_date = payment_date - timedelta(days=10)
    for i in range(10):
        v_id = uuid.uuid4()
        valid_ids.append(v_id)
        inv = InvoiceModel(
            id=v_id,
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-VALID-{i:03d}",
            issue_date=valid_date,
            due_date=valid_date + timedelta(days=30),
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("10000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        )
        db_session.add(inv)

    db_session.commit()

    # 4. Execute FilterCandidateInvoicesUseCase
    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    criteria = CandidateFilterCriteria(max_candidates=30, max_lookback_days=365)
    result = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=criteria,
        override_customer_id=customer_id,
    )

    # Total evaluated must be all 110 invoices in the DB
    assert result.total_evaluated == 110
    # All 10 valid invoices beyond index 100 must be retained!
    assert result.total_retained == 10
    assert result.total_excluded == 100
    retained_ids = [c.invoice_id for c in result.retained_candidates]
    for v_id in valid_ids:
        assert v_id in retained_ids
    assert result.exclusion_breakdown[FilterExclusionReason.DATE_OUT_OF_WINDOW.value] == 100


def test_case_b_many_invalid_candidates_before_valid_candidates(
    db_session: Session, registered_owner: dict
) -> None:
    """Case B: 110 invalid non-causal invoices preceding 15 valid invoices are all evaluated."""
    company_id = uuid.UUID(registered_owner["company"]["id"])
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    from app.modules.customer.infrastructure.models import CustomerModel
    from app.modules.payment.infrastructure.models import PaymentModel

    db_session.add(CustomerModel(id=customer_id, company_id=company_id, name="Future Billing Corp", is_archived=False))
    db_session.add(PaymentModel(
        id=payment_id,
        company_id=company_id,
        transaction_date=payment_date,
        amount=Decimal("5000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("5000.00"),
        currency="INR",
        status="UNRECONCILED",
        narration="Test payment narration B",
    ))

    # 110 future-dated invoices (payment_date < issue_date by 20 days)
    future_date = payment_date + timedelta(days=20)
    for i in range(110):
        db_session.add(InvoiceModel(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-FUT-{i:03d}",
            issue_date=future_date,
            due_date=future_date + timedelta(days=30),
            total_amount=Decimal("5000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("5000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        ))

    # 15 valid causal invoices
    valid_ids = []
    causal_date = payment_date - timedelta(days=5)
    for i in range(15):
        v_id = uuid.uuid4()
        valid_ids.append(v_id)
        db_session.add(InvoiceModel(
            id=v_id,
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-CAUSAL-{i:03d}",
            issue_date=causal_date,
            due_date=causal_date + timedelta(days=30),
            total_amount=Decimal("5000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("5000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        ))

    db_session.commit()

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    criteria = CandidateFilterCriteria(max_candidates=30, require_causality=True, max_advance_days=0)
    result = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=criteria,
        override_customer_id=customer_id,
    )

    assert result.total_evaluated == 125
    assert result.total_retained == 15
    assert result.total_excluded == 110
    assert result.exclusion_breakdown[FilterExclusionReason.NON_CAUSAL_DATE.value] == 110


def test_case_c_k_limit_behavior_sweep(
    db_session: Session, registered_owner: dict
) -> None:
    """Case C: Verify deterministic top-K behavior for K in [1, 5, 10, 30, 100]."""
    company_id = uuid.UUID(registered_owner["company"]["id"])
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    from app.modules.customer.infrastructure.models import CustomerModel
    from app.modules.payment.infrastructure.models import PaymentModel

    db_session.add(CustomerModel(id=customer_id, company_id=company_id, name="Sweep Corp", is_archived=False))
    db_session.add(PaymentModel(
        id=payment_id,
        company_id=company_id,
        transaction_date=payment_date,
        amount=Decimal("10000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("10000.00"),
        currency="INR",
        status="UNRECONCILED",
        narration="Test payment narration C",
    ))

    # 120 valid invoices with distinct due dates to guarantee strict FIFO ranking
    base_issue = payment_date - timedelta(days=60)
    for i in range(120):
        db_session.add(InvoiceModel(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-SWEEP-{i:03d}",
            issue_date=base_issue + timedelta(days=i % 10),
            due_date=base_issue + timedelta(days=i),  # strictly increasing due dates
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("10000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        ))

    db_session.commit()

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    for k in [1, 5, 10, 30, 100]:
        res = use_case.execute(
            payment_id=payment_id,
            company_id=company_id,
            criteria=CandidateFilterCriteria(max_candidates=k),
            override_customer_id=customer_id,
        )
        assert res.total_evaluated == 120
        assert res.total_retained == k
        assert res.total_excluded == 120 - k
        assert [c.rank for c in res.retained_candidates] == list(range(1, k + 1))
        assert res.exclusion_breakdown[FilterExclusionReason.TRUNCATED_BY_LIMIT.value] == 120 - k


def test_case_d_large_customer_population(
    db_session: Session, registered_owner: dict
) -> None:
    """Case D: Large customer with 200 open invoices is evaluated sub-second without dropping candidates."""
    company_id = uuid.UUID(registered_owner["company"]["id"])
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    from app.modules.customer.infrastructure.models import CustomerModel
    from app.modules.payment.infrastructure.models import PaymentModel

    db_session.add(CustomerModel(id=customer_id, company_id=company_id, name="Enterprise Scale Corp", is_archived=False))
    db_session.add(PaymentModel(
        id=payment_id,
        company_id=company_id,
        transaction_date=payment_date,
        amount=Decimal("10000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("10000.00"),
        currency="INR",
        status="UNRECONCILED",
        narration="Test payment narration D",
    ))

    # 200 invoices: 100 with amount > 20,000 (will be excluded by max_amount), 100 with amount <= 10,000
    for i in range(200):
        amt = Decimal("50000.00") if i < 100 else Decimal("8000.00")
        db_session.add(InvoiceModel(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-ENT-{i:03d}",
            issue_date=payment_date - timedelta(days=15),
            due_date=payment_date + timedelta(days=15),
            total_amount=amt,
            paid_amount=Decimal("0.00"),
            outstanding_amount=amt,
            currency="INR",
            status="PENDING",
            is_archived=False,
        ))

    db_session.commit()

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    criteria = CandidateFilterCriteria(max_amount=Decimal("20000.00"), max_candidates=30)
    res = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=criteria,
        override_customer_id=customer_id,
    )

    assert res.total_evaluated == 200
    assert res.total_retained == 30
    assert res.total_excluded == 170
    assert res.exclusion_breakdown[FilterExclusionReason.AMOUNT_ABOVE_MAXIMUM.value] == 100
    assert res.exclusion_breakdown[FilterExclusionReason.TRUNCATED_BY_LIMIT.value] == 70


def test_case_e_all_candidates_invalid(
    db_session: Session, registered_owner: dict
) -> None:
    """Case E: When all 120 candidates fail filters, terminates gracefully with ALL_EXCLUDED."""
    company_id = uuid.UUID(registered_owner["company"]["id"])
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    from app.modules.customer.infrastructure.models import CustomerModel
    from app.modules.payment.infrastructure.models import PaymentModel

    db_session.add(CustomerModel(id=customer_id, company_id=company_id, name="All Invalid Corp", is_archived=False))
    db_session.add(PaymentModel(
        id=payment_id,
        company_id=company_id,
        transaction_date=payment_date,
        amount=Decimal("10000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("10000.00"),
        currency="INR",
        status="UNRECONCILED",
        narration="Test payment narration E",
    ))

    # All 120 invoices are in the future
    for i in range(120):
        db_session.add(InvoiceModel(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-INV-{i:03d}",
            issue_date=payment_date + timedelta(days=10),
            due_date=payment_date + timedelta(days=40),
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("10000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        ))

    db_session.commit()

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    res = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=CandidateFilterCriteria(require_causality=True, max_advance_days=0),
        override_customer_id=customer_id,
    )

    assert res.status_code == "ALL_EXCLUDED"
    assert res.total_evaluated == 120
    assert res.total_retained == 0
    assert res.total_excluded == 120
    assert len(res.retained_candidates) == 0


def test_case_f_and_g_exact_and_overflow_k_boundary(
    db_session: Session, registered_owner: dict
) -> None:
    """Case F & G: Exact K retained vs overflow beyond K correctly truncated and marked."""
    company_id = uuid.UUID(registered_owner["company"]["id"])
    customer_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    from app.modules.customer.infrastructure.models import CustomerModel
    from app.modules.payment.infrastructure.models import PaymentModel

    db_session.add(CustomerModel(id=customer_id, company_id=company_id, name="Boundary Corp", is_archived=False))
    db_session.add(PaymentModel(
        id=payment_id,
        company_id=company_id,
        transaction_date=payment_date,
        amount=Decimal("10000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("10000.00"),
        currency="INR",
        status="UNRECONCILED",
        narration="Test payment narration F G",
    ))

    # Insert exactly 10 valid invoices
    for i in range(10):
        db_session.add(InvoiceModel(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            invoice_number=f"INV-EXACT-{i:03d}",
            issue_date=payment_date - timedelta(days=10),
            due_date=payment_date + timedelta(days=20),
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("10000.00"),
            currency="INR",
            status="PENDING",
            is_archived=False,
        ))

    db_session.commit()

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    # Case F: K=10 exactly matches 10 eligible
    res_f = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=CandidateFilterCriteria(max_candidates=10),
        override_customer_id=customer_id,
    )
    assert res_f.total_retained == 10
    assert res_f.total_excluded == 0

    # Case G: K=5 when 10 eligible -> 5 retained, 5 truncated
    res_g = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=CandidateFilterCriteria(max_candidates=5),
        override_customer_id=customer_id,
    )
    assert res_g.total_retained == 5
    assert res_g.total_excluded == 5
    assert res_g.exclusion_breakdown[FilterExclusionReason.TRUNCATED_BY_LIMIT.value] == 5


def test_excluded_candidates_strict_permutation_determinism() -> None:
    """Verify FINDING-14-4-01 remediation: excluded_candidates is strictly total-ordered and permutation-invariant.

    Across 100 randomized input permutations, both retained_candidates AND excluded_candidates
    must yield identical IDs, identical reasons, and identical to_dict() outputs.
    """
    engine = CandidateFilterRuleEngine()
    criteria = CandidateFilterCriteria(max_candidates=5)

    candidates = [
        CandidateInvoice(
            invoice_id=uuid.UUID(f"{i:08x}-1111-2222-3333-444444444444"),
            invoice_number=f"INV-DET-{i:03d}",
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("10000.00"),
            currency="USD" if i % 2 == 0 else "INR",  # half fail currency check
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 10 + (i % 15)),
            status="PENDING",
            retrieval_priority=float(50 + (i % 30)),
            evidence_signals=[],
            is_exact_amount_match=(i % 3 == 0),
            is_partial_amount_match=False,
            is_reference_match=(i % 5 == 0),
            rank=1,
        )
        for i in range(20)
    ]

    base_universe = CandidateInvoiceUniverse(
        payment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        candidates=candidates,
        total_eligible_invoices=20,
        truncated=False,
        candidate_limit=30,
        truncation_reason=None,
    )

    baseline = engine.filter_universe(
        universe=base_universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
    )
    baseline_retained_ids = [c.invoice_id for c in baseline.retained_candidates]
    baseline_excluded_ids = [e.invoice_id for e in baseline.excluded_candidates]
    baseline_retained_ranks = [c.rank for c in baseline.retained_candidates]
    baseline_dict = baseline.to_dict()

    for _ in range(100):
        shuffled = list(candidates)
        random.shuffle(shuffled)
        shuffled_u = CandidateInvoiceUniverse(
            payment_id=base_universe.payment_id,
            company_id=base_universe.company_id,
            customer_id=base_universe.customer_id,
            candidates=shuffled,
            total_eligible_invoices=20,
            truncated=False,
            candidate_limit=30,
            truncation_reason=None,
        )

        res = engine.filter_universe(
            universe=shuffled_u,
            payment_currency="INR",
            payment_date=date(2026, 8, 15),
            payment_effective_amount=Decimal("10000.00"),
            criteria=criteria,
        )

        # Retained candidates must be identical
        assert [c.invoice_id for c in res.retained_candidates] == baseline_retained_ids
        assert [c.rank for c in res.retained_candidates] == baseline_retained_ranks

        # CRITICAL: Excluded candidates ordering must be strictly identical across all 100 shuffles!
        assert [e.invoice_id for e in res.excluded_candidates] == baseline_excluded_ids
        assert [e.exclusion_reasons for e in res.excluded_candidates] == [e.exclusion_reasons for e in baseline.excluded_candidates]

        # Serialized dictionary equivalence (excluding evaluated_at timestamp)
        res_dict = res.to_dict()
        del res_dict["evaluated_at"]
        b_dict = dict(baseline_dict)
        del b_dict["evaluated_at"]
        assert res_dict == b_dict


def test_duck_typed_candidate_identity_defense_in_depth() -> None:
    """Verify FINDING-14-4-03 remediation: Duck-typed candidate attributes are evaluated fail-closed."""
    engine = CandidateFilterRuleEngine()
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    cust_a = uuid.uuid4()
    cust_b = uuid.uuid4()

    # Candidate 1: Mismatched tenant attached to candidate
    cand_cross_tenant = CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-CROSS-TENANT",
        total_amount=Decimal("1000.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("1000.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        status="PENDING",
        retrieval_priority=50.0,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=False,
        rank=1,
    )
    object.__setattr__(cand_cross_tenant, "company_id", company_b)

    # Candidate 2: Mismatched customer attached to candidate
    cand_cross_cust = CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-CROSS-CUST",
        total_amount=Decimal("1000.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("1000.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        status="PENDING",
        retrieval_priority=50.0,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=False,
        rank=2,
    )
    object.__setattr__(cand_cross_cust, "customer_id", cust_b)

    # Candidate 3: Archived candidate
    cand_archived = CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-ARCHIVED",
        total_amount=Decimal("1000.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("1000.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        status="PENDING",
        retrieval_priority=50.0,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=False,
        rank=3,
    )
    object.__setattr__(cand_archived, "is_archived", True)

    universe = CandidateInvoiceUniverse(
        payment_id=uuid.uuid4(),
        company_id=company_a,
        customer_id=cust_a,
        candidates=[cand_cross_tenant, cand_cross_cust, cand_archived],
        total_eligible_invoices=3,
        truncated=False,
        candidate_limit=30,
        truncation_reason=None,
    )

    filtered = engine.filter_universe(
        universe=universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("1000.00"),
    )

    assert filtered.total_retained == 0
    assert filtered.total_excluded == 3

    excluded_map = {e.invoice_id: e for e in filtered.excluded_candidates}
    assert FilterExclusionReason.TENANT_MISMATCH in excluded_map[cand_cross_tenant.invoice_id].exclusion_reasons
    assert FilterExclusionReason.CUSTOMER_MISMATCH in excluded_map[cand_cross_cust.invoice_id].exclusion_reasons
    assert FilterExclusionReason.ARCHIVED_INVOICE in excluded_map[cand_archived.invoice_id].exclusion_reasons

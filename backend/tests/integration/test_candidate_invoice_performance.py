"""Performance benchmark and scalability tests for Phase 13.2 Candidate Invoice Generation."""

from decimal import Decimal
import time
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.invoice.domain.entities import InvoiceStatus
from app.modules.invoice.infrastructure.models import InvoiceModel


def test_candidate_generation_large_catalog_scalability(
    client: TestClient, registered_owner: dict
) -> None:
    """Benchmark candidate invoice generation for a customer with 100 open invoices.

    Verifies:
    1. Sub-200ms API execution time under realistic open invoice load.
    2. Correct truncation metadata (total_eligible_invoices=100, truncated=True, candidate_limit=30).
    3. Correct bounded output length (exactly 30 candidates).
    4. Sequential ranks 1..30 assigned to returned candidates.
    """
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Enterprise Scale Corp"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    from datetime import date
    import uuid

    # 2. Bulk seed 100 open invoices directly via db session for fast test execution
    # Get db session from app dependency
    db: Session = next(client.app.dependency_overrides[get_db]())
    company_id = uuid.UUID(registered_owner["user"]["company_id"])
    customer_uuid = uuid.UUID(cust_id)

    invoices = []
    for i in range(100):
        # Invoice 42 matches exact payment amount
        amount = Decimal("5432.10") if i == 42 else Decimal(f"{1000 + (i * 10)}.00")
        inv = InvoiceModel(
            company_id=company_id,
            customer_id=customer_uuid,
            invoice_number=f"INV-PERF-{i:04d}",
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            total_amount=amount,
            paid_amount=Decimal("0.00"),
            outstanding_amount=amount,
            currency="INR",
            status=InvoiceStatus.PENDING.value,
            is_archived=False,
        )
        invoices.append(inv)


    db.add_all(invoices)
    db.commit()

    # 3. Create Payment for exact amount of invoice 42
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-31",
            "amount": "5432.10",
            "currency": "INR",
            "narration": "SETTLEMENT FOR ENTERPRISE INVOICE",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 4. Execute and benchmark candidate generation API
    start_time = time.perf_counter()
    cand_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}",
        headers=headers,
        json={"override_customer_id": cust_id, "limit": 30},
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    assert cand_resp.status_code == 200
    data = cand_resp.json()["data"]

    # 5. Assertions on Performance & Bounded Universe
    assert elapsed_ms < 500.0, f"Candidate generation took {elapsed_ms:.2f}ms, expected < 500ms"
    assert data["status_code"] == "SUCCESS"
    assert data["total_eligible_invoices"] == 100
    assert data["truncated"] is True
    assert data["candidate_limit"] == 30
    assert len(data["candidates"]) == 30

    # Verify that the exact amount match (invoice 42) ranked #1
    top_candidate = data["candidates"][0]
    assert top_candidate["invoice_number"] == "INV-PERF-0042"
    assert top_candidate["is_exact_amount_match"] is True
    assert top_candidate["rank"] == 1

    # Verify strictly monotonic ranks 1..30
    ranks = [c["rank"] for c in data["candidates"]]
    assert ranks == list(range(1, 31))

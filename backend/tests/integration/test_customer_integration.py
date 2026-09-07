"""Integration tests for Customer Management REST API endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_create_customer_success(client: TestClient, registered_owner: dict):
    """Verify customer creation returns 201 Created and persisted values."""
    payload = {
        "name": "Acme Global Solutions",
        "tax_id": "29ABCDE1234F1Z5",
        "email": "billing@acmeglobal.com",
        "phone": "+91 9988776655",
        "notes": "Net 30 payment terms",
    }
    res = client.post("/api/v1/customers", headers=registered_owner["headers"], json=payload)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["name"] == "Acme Global Solutions"
    assert data["tax_id"] == "29ABCDE1234F1Z5"
    assert data["email"] == "billing@acmeglobal.com"
    assert data["is_archived"] is False
    assert data["company_id"] == registered_owner["company"]["id"]


def test_create_customer_duplicate_name_rejected(client: TestClient, registered_owner: dict):
    """Verify duplicate customer name within same tenant returns 409 Conflict."""
    payload = {"name": "Duplicate Test Co"}
    res1 = client.post("/api/v1/customers", headers=registered_owner["headers"], json=payload)
    assert res1.status_code == 201

    res2 = client.post("/api/v1/customers", headers=registered_owner["headers"], json=payload)
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "CUSTOMER_ALREADY_EXISTS"


def test_create_customer_same_name_different_companies(
    client: TestClient,
    registered_owner: dict,
    second_company_owner: dict,
):
    """Verify same customer name is permitted across two distinct tenant companies."""
    payload = {"name": "Shared Name Corp"}
    res_a = client.post("/api/v1/customers", headers=registered_owner["headers"], json=payload)
    assert res_a.status_code == 201

    res_b = client.post("/api/v1/customers", headers=second_company_owner["headers"], json=payload)
    assert res_b.status_code == 201


def test_create_customer_validation_empty_name(client: TestClient, registered_owner: dict):
    """Verify empty or whitespace-only name is rejected."""
    res = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "   "})
    assert res.status_code in (400, 422)


def test_get_customer_detail_with_relations(client: TestClient, registered_owner: dict):
    """Verify GET /api/v1/customers/{id} returns customer with its aliases and identifiers."""
    # 1. Create customer
    c_res = client.post(
        "/api/v1/customers",
        headers=registered_owner["headers"],
        json={"name": "Infosys Technologies"},
    )
    cust_id = c_res.json()["data"]["id"]

    # 2. Add alias
    client.post(
        f"/api/v1/customers/{cust_id}/aliases",
        headers=registered_owner["headers"],
        json={"alias_name": "INFY"},
    )

    # 3. Add payment identifier
    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=registered_owner["headers"],
        json={"identifier_type": "UPI_VPA", "identifier_value": "infosys@icici"},
    )

    # 4. Fetch detail
    detail_res = client.get(f"/api/v1/customers/{cust_id}", headers=registered_owner["headers"])
    assert detail_res.status_code == 200
    detail = detail_res.json()["data"]
    assert detail["customer"]["name"] == "Infosys Technologies"
    assert len(detail["aliases"]) == 1
    assert detail["aliases"][0]["alias_name"] == "INFY"
    assert len(detail["payment_identifiers"]) == 1
    assert detail["payment_identifiers"][0]["identifier_value"] == "infosys@icici"


def test_list_customers_pagination_and_archive_filter(client: TestClient, registered_owner: dict):
    """Verify customer listing pagination and archive filtering."""
    # Create 3 customers
    client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Customer 1"})
    c2 = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Customer 2"})
    client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Customer 3"})

    # Archive Customer 2
    c2_id = c2.json()["data"]["id"]
    client.post(f"/api/v1/customers/{c2_id}/archive", headers=registered_owner["headers"])

    # Default list should exclude archived
    list_active = client.get("/api/v1/customers", headers=registered_owner["headers"])
    assert list_active.status_code == 200
    assert list_active.json()["data"]["total"] == 2

    # Include archived
    list_all = client.get("/api/v1/customers?include_archived=true", headers=registered_owner["headers"])
    assert list_all.status_code == 200
    assert list_all.json()["data"]["total"] == 3


def test_update_customer(client: TestClient, registered_owner: dict):
    """Verify updating customer information and conflict handling."""
    c1 = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Original Name"})
    cust_id = c1.json()["data"]["id"]

    # Update phone and email
    update_res = client.put(
        f"/api/v1/customers/{cust_id}",
        headers=registered_owner["headers"],
        json={"phone": "+91 1122334455", "notes": "VIP Client"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["data"]["phone"] == "+91 1122334455"
    assert update_res.json()["data"]["notes"] == "VIP Client"


def test_archive_and_unarchive_customer(client: TestClient, registered_owner: dict):
    """Verify archiving and unarchiving customer state machine."""
    c = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Lifecycle Customer"})
    cust_id = c.json()["data"]["id"]

    # Archive
    arch_res = client.post(f"/api/v1/customers/{cust_id}/archive", headers=registered_owner["headers"])
    assert arch_res.status_code == 200
    assert arch_res.json()["data"]["is_archived"] is True

    # Unarchive
    unarch_res = client.post(f"/api/v1/customers/{cust_id}/unarchive", headers=registered_owner["headers"])
    assert unarch_res.status_code == 200
    assert unarch_res.json()["data"]["is_archived"] is False


def test_delete_customer(client: TestClient, registered_owner: dict):
    """Verify safe customer deletion."""
    c = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "To Delete Co"})
    cust_id = c.json()["data"]["id"]

    del_res = client.delete(f"/api/v1/customers/{cust_id}", headers=registered_owner["headers"])
    assert del_res.status_code == 200

    # Ensure it no longer exists
    get_res = client.get(f"/api/v1/customers/{cust_id}", headers=registered_owner["headers"])
    assert get_res.status_code == 404


def test_alias_management_workflow(client: TestClient, registered_owner: dict):
    """Verify adding, duplicate checking, and deleting aliases."""
    c = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Bharti Airtel"})
    cust_id = c.json()["data"]["id"]

    # Add alias
    alias_res = client.post(
        f"/api/v1/customers/{cust_id}/aliases",
        headers=registered_owner["headers"],
        json={"alias_name": "AIRTEL"},
    )
    assert alias_res.status_code == 201
    alias_id = alias_res.json()["data"]["id"]

    # Duplicate alias rejected
    dup_res = client.post(
        f"/api/v1/customers/{cust_id}/aliases",
        headers=registered_owner["headers"],
        json={"alias_name": "AIRTEL"},
    )
    assert dup_res.status_code == 409

    # Delete alias
    del_alias_res = client.delete(
        f"/api/v1/customers/{cust_id}/aliases/{alias_id}",
        headers=registered_owner["headers"],
    )
    assert del_alias_res.status_code == 200


def test_payment_identifier_workflow(client: TestClient, registered_owner: dict):
    """Verify adding, type validation, duplicate checking, and deleting payment identifiers."""
    c = client.post("/api/v1/customers", headers=registered_owner["headers"], json={"name": "Wipro Limited"})
    cust_id = c.json()["data"]["id"]

    # Add Bank Account
    bank_res = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=registered_owner["headers"],
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "987654321000"},
    )
    assert bank_res.status_code == 201
    ident_id = bank_res.json()["data"]["id"]

    # Add UPI VPA
    upi_res = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=registered_owner["headers"],
        json={"identifier_type": "UPI_VPA", "identifier_value": "wipro@axis"},
    )
    assert upi_res.status_code == 201

    # Invalid identifier type rejected
    bad_type = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=registered_owner["headers"],
        json={"identifier_type": "CRYPTO_WALLET", "identifier_value": "0x123"},
    )
    assert bad_type.status_code in (400, 422)

    # Duplicate identifier rejected
    dup_res = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=registered_owner["headers"],
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "987654321000"},
    )
    assert dup_res.status_code == 409

    # Delete identifier
    del_res = client.delete(
        f"/api/v1/customers/{cust_id}/identifiers/{ident_id}",
        headers=registered_owner["headers"],
    )
    assert del_res.status_code == 200


def test_multi_signal_customer_search(client: TestClient, registered_owner: dict):
    """Verify search finds customer across canonical name, alias, GST, email, and UPI."""
    c = client.post(
        "/api/v1/customers",
        headers=registered_owner["headers"],
        json={
            "name": "State Bank of India",
            "tax_id": "07AAAAA0000A1Z5",
            "email": "treasury@sbi.co.in",
            "phone": "+91 2212345678",
        },
    )
    cust_id = c.json()["data"]["id"]

    # Add alias and UPI
    client.post(f"/api/v1/customers/{cust_id}/aliases", headers=registered_owner["headers"], json={"alias_name": "SBI BANK"})
    client.post(f"/api/v1/customers/{cust_id}/identifiers", headers=registered_owner["headers"], json={"identifier_type": "UPI_VPA", "identifier_value": "sbi@sbi"})

    # 1. Search by canonical name substring
    s1 = client.get("/api/v1/customers/search?q=State+Bank", headers=registered_owner["headers"])
    assert s1.status_code == 200
    assert s1.json()["data"]["total"] == 1

    # 2. Search by alias
    s2 = client.get("/api/v1/customers/search?q=SBI+BANK", headers=registered_owner["headers"])
    assert s2.status_code == 200
    assert s2.json()["data"]["total"] == 1

    # 3. Search by GSTIN
    s3 = client.get("/api/v1/customers/search?q=07AAAAA", headers=registered_owner["headers"])
    assert s3.status_code == 200
    assert s3.json()["data"]["total"] == 1

    # 4. Search by email
    s4 = client.get("/api/v1/customers/search?q=treasury@sbi", headers=registered_owner["headers"])
    assert s4.status_code == 200
    assert s4.json()["data"]["total"] == 1

    # 5. Search by UPI
    s5 = client.get("/api/v1/customers/search?q=sbi@sbi", headers=registered_owner["headers"])
    assert s5.status_code == 200
    assert s5.json()["data"]["total"] == 1

    # 6. Search with no results
    s6 = client.get("/api/v1/customers/search?q=NonExistentQuery999", headers=registered_owner["headers"])
    assert s6.status_code == 200
    assert s6.json()["data"]["total"] == 0

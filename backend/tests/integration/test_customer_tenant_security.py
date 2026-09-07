"""Multi-tenant security and cross-tenant isolation tests for Customer Management."""

import uuid
import pytest
from fastapi.testclient import TestClient


def test_customer_cross_tenant_isolation(
    client: TestClient,
    registered_owner: dict,
    second_company_owner: dict,
):
    """Verify Company B cannot view, edit, archive, or delete Company A's customer."""
    # 1. Company A creates customer
    create_res = client.post(
        "/api/v1/customers",
        headers=registered_owner["headers"],
        json={"name": "Company A Private Customer", "tax_id": "29A11111111A1Z1"},
    )
    assert create_res.status_code == 201
    cust_a_id = create_res.json()["data"]["id"]

    # 2. Company A adds alias & identifier
    alias_res = client.post(
        f"/api/v1/customers/{cust_a_id}/aliases",
        headers=registered_owner["headers"],
        json={"alias_name": "CORP-A-SECRET"},
    )
    alias_id = alias_res.json()["data"]["id"]

    ident_res = client.post(
        f"/api/v1/customers/{cust_a_id}/identifiers",
        headers=registered_owner["headers"],
        json={"identifier_type": "UPI_VPA", "identifier_value": "corpa@bank"},
    )
    ident_id = ident_res.json()["data"]["id"]

    # 3. Company B attempts to GET Customer A -> 404 Not Found
    get_res = client.get(f"/api/v1/customers/{cust_a_id}", headers=second_company_owner["headers"])
    assert get_res.status_code == 404

    # 4. Company B attempts to UPDATE Customer A -> 404 Not Found
    update_res = client.put(
        f"/api/v1/customers/{cust_a_id}",
        headers=second_company_owner["headers"],
        json={"name": "Hacked Name"},
    )
    assert update_res.status_code == 404

    # 5. Company B attempts to ARCHIVE Customer A -> 404 Not Found
    arch_res = client.post(
        f"/api/v1/customers/{cust_a_id}/archive",
        headers=second_company_owner["headers"],
    )
    assert arch_res.status_code == 404

    # 6. Company B attempts to DELETE Customer A -> 404 Not Found
    del_res = client.delete(
        f"/api/v1/customers/{cust_a_id}",
        headers=second_company_owner["headers"],
    )
    assert del_res.status_code == 404

    # 7. Company B attempts to ADD ALIAS to Customer A -> 404 Not Found
    add_alias = client.post(
        f"/api/v1/customers/{cust_a_id}/aliases",
        headers=second_company_owner["headers"],
        json={"alias_name": "INTRUDER"},
    )
    assert add_alias.status_code == 404

    # 8. Company B attempts to DELETE ALIAS from Customer A -> 404 Not Found
    del_alias = client.delete(
        f"/api/v1/customers/{cust_a_id}/aliases/{alias_id}",
        headers=second_company_owner["headers"],
    )
    assert del_alias.status_code == 404

    # 9. Company B attempts to ADD IDENTIFIER to Customer A -> 404 Not Found
    add_ident = client.post(
        f"/api/v1/customers/{cust_a_id}/identifiers",
        headers=second_company_owner["headers"],
        json={"identifier_type": "UPI_VPA", "identifier_value": "intruder@bank"},
    )
    assert add_ident.status_code == 404

    # 10. Company B attempts to DELETE IDENTIFIER from Customer A -> 404 Not Found
    del_ident = client.delete(
        f"/api/v1/customers/{cust_a_id}/identifiers/{ident_id}",
        headers=second_company_owner["headers"],
    )
    assert del_ident.status_code == 404

    # 11. Company B searches for Customer A name -> returns 0 items
    search_res = client.get(
        "/api/v1/customers/search?q=Company+A+Private",
        headers=second_company_owner["headers"],
    )
    assert search_res.status_code == 200
    assert search_res.json()["data"]["total"] == 0


def test_unauthenticated_customer_endpoints_rejected(client: TestClient):
    """Verify all customer endpoints reject unauthenticated requests with 401 or 403."""
    dummy_id = uuid.uuid4()
    endpoints = [
        ("GET", "/api/v1/customers"),
        ("POST", "/api/v1/customers"),
        ("GET", f"/api/v1/customers/{dummy_id}"),
        ("PUT", f"/api/v1/customers/{dummy_id}"),
        ("POST", f"/api/v1/customers/{dummy_id}/archive"),
        ("DELETE", f"/api/v1/customers/{dummy_id}"),
        ("POST", f"/api/v1/customers/{dummy_id}/aliases"),
        ("POST", f"/api/v1/customers/{dummy_id}/identifiers"),
    ]

    for method, path in endpoints:
        if method == "GET":
            res = client.get(path)
        elif method == "POST":
            res = client.post(path, json={})
        elif method == "PUT":
            res = client.put(path, json={})
        elif method == "DELETE":
            res = client.delete(path)
        assert res.status_code in (401, 403), f"Endpoint {method} {path} did not reject unauthenticated access"

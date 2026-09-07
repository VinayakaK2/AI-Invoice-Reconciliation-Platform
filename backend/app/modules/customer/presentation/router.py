"""Customer module presentation router.

Provides REST endpoints for customer lifecycles, aliases, payment identifiers, and search.
"""

from typing import Any, Dict
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.domain.entities import User
from app.modules.auth.presentation.dependencies import get_current_user
from app.modules.customer.application.use_cases import (
    AddCustomerAliasUseCase,
    AddPaymentIdentifierUseCase,
    ArchiveCustomerUseCase,
    CreateCustomerUseCase,
    DeleteCustomerUseCase,
    GetCustomerDetailUseCase,
    ListCustomersUseCase,
    RemoveCustomerAliasUseCase,
    RemovePaymentIdentifierUseCase,
    SearchCustomersUseCase,
    UnarchiveCustomerUseCase,
    UpdateCustomerUseCase,
)
from app.modules.customer.infrastructure.repositories import (
    CustomerAliasRepository,
    CustomerPaymentIdentifierRepository,
    CustomerRepository,
)
from app.modules.customer.presentation.schemas import (
    CustomerAliasCreateRequest,
    CustomerAliasResponse,
    CustomerCreateRequest,
    CustomerDetailResponse,
    CustomerListResponse,
    CustomerPaymentIdentifierCreateRequest,
    CustomerPaymentIdentifierResponse,
    CustomerResponse,
    CustomerUpdateRequest,
)

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("/status", summary="Customer module status probe")
def customer_module_status() -> Dict[str, str]:
    """Return customer module readiness status."""
    return {"module": "customer", "status": "initialized"}


@router.post(
    "",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new customer",
)
def create_customer(
    request: CustomerCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create a new customer within the authenticated company workspace."""
    repo = CustomerRepository(db)
    use_case = CreateCustomerUseCase(repo)
    customer = use_case.execute(current_user.company_id, request)

    return {
        "success": True,
        "data": CustomerResponse.model_validate(customer),
    }


@router.get(
    "",
    response_model=Dict[str, Any],
    summary="List customers belonging to caller's company",
)
def list_customers(
    include_archived: bool = Query(default=False, description="Include archived customers"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List and paginate customers for current company workspace."""
    repo = CustomerRepository(db)
    use_case = ListCustomersUseCase(repo)
    items, total = use_case.execute(
        company_id=current_user.company_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )

    return {
        "success": True,
        "data": CustomerListResponse(
            items=[CustomerResponse.model_validate(c) for c in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
    }


@router.get(
    "/search",
    response_model=Dict[str, Any],
    summary="Search customers by name, alias, GST, or identifiers",
)
def search_customers(
    q: str = Query(..., min_length=1, description="Search term"),
    include_archived: bool = Query(default=False, description="Include archived customers"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search customers across names, aliases, GSTIN, phone, and payment identifiers."""
    repo = CustomerRepository(db)
    use_case = SearchCustomersUseCase(repo)
    items, total = use_case.execute(
        company_id=current_user.company_id,
        query_str=q,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )

    return {
        "success": True,
        "data": CustomerListResponse(
            items=[CustomerResponse.model_validate(c) for c in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
    }


@router.get(
    "/{customer_id}",
    response_model=Dict[str, Any],
    summary="Get customer details including aliases and payment identifiers",
)
def get_customer_detail(
    customer_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Fetch complete customer identity record with aliases and payment identifiers."""
    cust_repo = CustomerRepository(db)
    alias_repo = CustomerAliasRepository(db)
    ident_repo = CustomerPaymentIdentifierRepository(db)
    use_case = GetCustomerDetailUseCase(cust_repo, alias_repo, ident_repo)

    customer, aliases, idents = use_case.execute(customer_id, current_user.company_id)
    return {
        "success": True,
        "data": CustomerDetailResponse(
            customer=CustomerResponse.model_validate(customer),
            aliases=[CustomerAliasResponse.model_validate(a) for a in aliases],
            payment_identifiers=[CustomerPaymentIdentifierResponse.model_validate(i) for i in idents],
        ),
    }


@router.put(
    "/{customer_id}",
    response_model=Dict[str, Any],
    summary="Update customer details",
)
def update_customer(
    customer_id: UUID,
    request: CustomerUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Update customer profile information."""
    repo = CustomerRepository(db)
    use_case = UpdateCustomerUseCase(repo)
    customer = use_case.execute(customer_id, current_user.company_id, request)

    return {
        "success": True,
        "data": CustomerResponse.model_validate(customer),
    }


@router.post(
    "/{customer_id}/archive",
    response_model=Dict[str, Any],
    summary="Archive customer",
)
def archive_customer(
    customer_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Archive customer without destroying historical records."""
    repo = CustomerRepository(db)
    use_case = ArchiveCustomerUseCase(repo)
    customer = use_case.execute(customer_id, current_user.company_id)

    return {
        "success": True,
        "data": CustomerResponse.model_validate(customer),
    }


@router.post(
    "/{customer_id}/unarchive",
    response_model=Dict[str, Any],
    summary="Unarchive customer",
)
def unarchive_customer(
    customer_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Restore archived customer to active status."""
    repo = CustomerRepository(db)
    use_case = UnarchiveCustomerUseCase(repo)
    customer = use_case.execute(customer_id, current_user.company_id)

    return {
        "success": True,
        "data": CustomerResponse.model_validate(customer),
    }


@router.delete(
    "/{customer_id}",
    response_model=Dict[str, Any],
    summary="Delete customer",
)
def delete_customer(
    customer_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Delete a customer record."""
    repo = CustomerRepository(db)
    use_case = DeleteCustomerUseCase(repo)
    use_case.execute(customer_id, current_user.company_id)

    return {
        "success": True,
        "data": {"message": "Customer successfully deleted"},
    }


@router.post(
    "/{customer_id}/aliases",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Add an alternate name alias to customer",
)
def add_customer_alias(
    customer_id: UUID,
    request: CustomerAliasCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Add a statement name alias for counterparty resolution."""
    cust_repo = CustomerRepository(db)
    alias_repo = CustomerAliasRepository(db)
    use_case = AddCustomerAliasUseCase(cust_repo, alias_repo)

    alias = use_case.execute(customer_id, current_user.company_id, request)
    return {
        "success": True,
        "data": CustomerAliasResponse.model_validate(alias),
    }


@router.delete(
    "/{customer_id}/aliases/{alias_id}",
    response_model=Dict[str, Any],
    summary="Remove a customer alias",
)
def remove_customer_alias(
    customer_id: UUID,
    alias_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Remove a statement name alias."""
    cust_repo = CustomerRepository(db)
    alias_repo = CustomerAliasRepository(db)
    use_case = RemoveCustomerAliasUseCase(cust_repo, alias_repo)

    use_case.execute(customer_id, alias_id, current_user.company_id)
    return {
        "success": True,
        "data": {"message": "Alias successfully removed"},
    }


@router.post(
    "/{customer_id}/identifiers",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Associate a bank account or UPI ID with customer",
)
def add_payment_identifier(
    customer_id: UUID,
    request: CustomerPaymentIdentifierCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Associate a bank account, virtual account, or UPI VPA with customer."""
    cust_repo = CustomerRepository(db)
    ident_repo = CustomerPaymentIdentifierRepository(db)
    use_case = AddPaymentIdentifierUseCase(cust_repo, ident_repo)

    identifier = use_case.execute(customer_id, current_user.company_id, request)
    return {
        "success": True,
        "data": CustomerPaymentIdentifierResponse.model_validate(identifier),
    }


@router.delete(
    "/{customer_id}/identifiers/{identifier_id}",
    response_model=Dict[str, Any],
    summary="Remove a payment identifier from customer",
)
def remove_payment_identifier(
    customer_id: UUID,
    identifier_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Remove a payment identifier mapping."""
    cust_repo = CustomerRepository(db)
    ident_repo = CustomerPaymentIdentifierRepository(db)
    use_case = RemovePaymentIdentifierUseCase(cust_repo, ident_repo)

    use_case.execute(customer_id, identifier_id, current_user.company_id)
    return {
        "success": True,
        "data": {"message": "Payment identifier successfully removed"},
    }

"""Pydantic v2 schemas and validation contracts for Customer Management."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CustomerCreateRequest(BaseModel):
    """Payload for creating a new customer counterparty."""
    name: str = Field(..., min_length=1, max_length=255, description="Legal or trade name of the customer")
    tax_id: Optional[str] = Field(default=None, max_length=50, description="Customer GSTIN or PAN identifier")
    email: Optional[EmailStr] = Field(default=None, description="Billing contact email address")
    phone: Optional[str] = Field(default=None, max_length=50, description="Contact phone or mobile number")
    notes: Optional[str] = Field(default=None, max_length=1000, description="Internal notes regarding payment handling")


class CustomerUpdateRequest(BaseModel):
    """Payload for updating an existing customer record."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    tax_id: Optional[str] = Field(default=None, max_length=50)
    email: Optional[EmailStr] = Field(default=None)
    phone: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=1000)


class CustomerResponse(BaseModel):
    """Serialized customer representation."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    name: str
    tax_id: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    notes: Optional[str]
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class CustomerAliasCreateRequest(BaseModel):
    """Payload for adding an alternate trade name or statement alias to a customer."""
    alias_name: str = Field(..., min_length=1, max_length=255, description="Alternate spelling or name on statement")


class CustomerAliasResponse(BaseModel):
    """Serialized customer alias representation."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    customer_id: UUID
    alias_name: str
    created_at: datetime


class CustomerPaymentIdentifierCreateRequest(BaseModel):
    """Payload for associating a known bank account or UPI ID with a customer."""
    identifier_type: str = Field(..., description="BANK_ACCOUNT, VIRTUAL_ACCOUNT, or UPI_VPA")
    identifier_value: str = Field(..., min_length=1, max_length=255, description="Account number or UPI VPA")


class CustomerPaymentIdentifierResponse(BaseModel):
    """Serialized payment identifier representation."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    customer_id: UUID
    identifier_type: str
    identifier_value: str
    is_active: bool
    created_at: datetime


class CustomerDetailResponse(BaseModel):
    """Complete detail representation of a customer including all identity signals."""
    customer: CustomerResponse
    aliases: List[CustomerAliasResponse]
    payment_identifiers: List[CustomerPaymentIdentifierResponse]


class CustomerListResponse(BaseModel):
    """Paginated list envelope for customers."""
    items: List[CustomerResponse]
    total: int
    limit: int
    offset: int

"""Application ports defining boundaries for customer and payment data access."""

from abc import ABC, abstractmethod
from typing import List, Optional
from uuid import UUID

from app.modules.reconciliation.domain.invoice_rules import InvoiceCandidateContext
from app.modules.reconciliation.domain.rules import (
    CustomerLookupContext,
    PaymentIntakeContext,
)


class CustomerLookupPort(ABC):
    """Port for retrieving tenant-isolated customer lookup context."""

    @abstractmethod
    def get_active_customer_contexts(
        self, company_id: UUID
    ) -> List[CustomerLookupContext]:
        """Fetch all non-archived customers with their aliases and payment identifiers for a tenant."""
        raise NotImplementedError

    @abstractmethod
    def get_customer_by_id(
        self, customer_id: UUID, company_id: UUID
    ) -> Optional[CustomerLookupContext]:
        """Fetch a specific customer's lookup context within tenant boundary."""
        raise NotImplementedError


class PaymentLookupPort(ABC):
    """Port for retrieving tenant-isolated payment intake context."""

    @abstractmethod
    def get_payment_intake_context(
        self, payment_id: UUID, company_id: UUID
    ) -> Optional[PaymentIntakeContext]:
        """Fetch a specific payment's intake context, including linked bank transaction metadata."""
        raise NotImplementedError

    @abstractmethod
    def list_unreconciled_payment_contexts(
        self, company_id: UUID, limit: int = 50
    ) -> List[PaymentIntakeContext]:
        """Fetch unreconciled payment intake contexts for batch counterparty identification."""
        raise NotImplementedError

    @abstractmethod
    def list_eligible_intake_contexts(
        self, company_id: UUID, limit: int = 50
    ) -> List[PaymentIntakeContext]:
        """Fetch eligible (UNRECONCILED / PARTIALLY_RECONCILED with balance > 0) payment intake contexts."""
        raise NotImplementedError


class InvoiceLookupPort(ABC):
    """Port for retrieving tenant-isolated open invoices for candidate generation."""

    @abstractmethod
    def get_customer_candidate_invoices(
        self,
        company_id: UUID,
        customer_id: UUID,
        currency: str,
    ) -> List["InvoiceCandidateContext"]:
        """Fetch unarchived, open (PENDING/PARTIALLY_PAID) invoices matching customer and currency."""
        raise NotImplementedError

    @abstractmethod
    def count_customer_invoices_in_other_currencies(
        self,
        company_id: UUID,
        customer_id: UUID,
        payment_currency: str,
    ) -> int:
        """Count open invoices for customer in other currencies to detect currency mismatch."""
        raise NotImplementedError


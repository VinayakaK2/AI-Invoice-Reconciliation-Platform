"""SQLAlchemy database lookup adapters implementing application ports."""

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from uuid import UUID
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.customer.infrastructure.models import (
    CustomerAliasModel,
    CustomerModel,
    CustomerPaymentIdentifierModel,
)
from app.modules.invoice.domain.entities import InvoiceStatus
from app.modules.invoice.infrastructure.models import InvoiceModel
from app.modules.payment.infrastructure.models import PaymentModel
from app.modules.reconciliation.application.ports import (
    CustomerLookupPort,
    InvoiceLookupPort,
    PaymentLookupPort,
)
from app.modules.reconciliation.domain.invoice_rules import InvoiceCandidateContext
from app.modules.reconciliation.domain.rules import (
    CustomerLookupContext,
    PaymentIntakeContext,
)


class SQLAlchemyCustomerLookupAdapter(CustomerLookupPort):
    """Database adapter retrieving tenant-isolated customer contexts."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_active_customer_contexts(
        self, company_id: UUID
    ) -> List[CustomerLookupContext]:
        """Retrieve all active customers and their aliases/identifiers for a tenant."""
        # Query active customers scoped by company_id with deterministic ordering
        customers = (
            self.db.query(CustomerModel)
            .filter(
                CustomerModel.company_id == company_id,
                CustomerModel.is_archived.is_(False),
            )
            .order_by(CustomerModel.name.asc(), CustomerModel.id.asc())
            .all()
        )
        if not customers:
            return []

        cust_ids = [c.id for c in customers]

        # Query aliases scoped by company_id and customer_ids with deterministic ordering
        aliases = (
            self.db.query(CustomerAliasModel)
            .filter(
                CustomerAliasModel.company_id == company_id,
                CustomerAliasModel.customer_id.in_(cust_ids),
            )
            .order_by(CustomerAliasModel.alias_name.asc(), CustomerAliasModel.id.asc())
            .all()
        )
        alias_map: Dict[UUID, List[str]] = defaultdict(list)
        for a in aliases:
            alias_map[a.customer_id].append(a.alias_name)

        # Query active identifiers scoped by company_id and customer_ids with deterministic ordering
        identifiers = (
            self.db.query(CustomerPaymentIdentifierModel)
            .filter(
                CustomerPaymentIdentifierModel.company_id == company_id,
                CustomerPaymentIdentifierModel.customer_id.in_(cust_ids),
                CustomerPaymentIdentifierModel.is_active.is_(True),
            )
            .order_by(
                CustomerPaymentIdentifierModel.identifier_type.asc(),
                CustomerPaymentIdentifierModel.created_at.asc(),
                CustomerPaymentIdentifierModel.id.asc(),
            )
            .all()
        )
        identifier_map: Dict[UUID, List[Tuple[str, str]]] = defaultdict(list)
        for ident in identifiers:
            identifier_map[ident.customer_id].append(
                (ident.identifier_type, ident.identifier_value)
            )

        contexts: List[CustomerLookupContext] = []
        for cust in customers:
            contexts.append(
                CustomerLookupContext(
                    customer_id=cust.id,
                    name=cust.name,
                    tax_id=cust.tax_id,
                    is_archived=cust.is_archived,
                    aliases=alias_map.get(cust.id, []),
                    identifiers=identifier_map.get(cust.id, []),
                )
            )

        return contexts

    def get_customer_by_id(
        self, customer_id: UUID, company_id: UUID
    ) -> Optional[CustomerLookupContext]:
        """Retrieve a specific customer context within tenant boundary."""
        cust = (
            self.db.query(CustomerModel)
            .filter(
                CustomerModel.id == customer_id,
                CustomerModel.company_id == company_id,
            )
            .first()
        )
        if not cust:
            return None

        aliases = (
            self.db.query(CustomerAliasModel)
            .filter(
                CustomerAliasModel.company_id == company_id,
                CustomerAliasModel.customer_id == customer_id,
            )
            .all()
        )
        identifiers = (
            self.db.query(CustomerPaymentIdentifierModel)
            .filter(
                CustomerPaymentIdentifierModel.company_id == company_id,
                CustomerPaymentIdentifierModel.customer_id == customer_id,
                CustomerPaymentIdentifierModel.is_active.is_(True),
            )
            .all()
        )

        return CustomerLookupContext(
            customer_id=cust.id,
            name=cust.name,
            tax_id=cust.tax_id,
            is_archived=cust.is_archived,
            aliases=[a.alias_name for a in aliases],
            identifiers=[(i.identifier_type, i.identifier_value) for i in identifiers],
        )


class SQLAlchemyPaymentLookupAdapter(PaymentLookupPort):
    """Database adapter retrieving tenant-isolated payment intake contexts."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_payment_intake_context(
        self, payment_id: UUID, company_id: UUID
    ) -> Optional[PaymentIntakeContext]:
        """Fetch a single payment intake context within tenant boundary."""
        payment = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.id == payment_id,
                PaymentModel.company_id == company_id,
            )
            .first()
        )
        if not payment:
            return None

        # Resolve joined bank transaction metadata fallbacks
        txn = payment.bank_transaction
        payer_raw_name = payment.payer_raw_name or (txn.counterparty_name if txn else None)
        payment_reference = payment.reference_number or (txn.reference_number if txn else None)
        bank_account_number = payment.bank_account_number or (txn.bank_account_number if txn else None)
        narration = payment.narration or (txn.narration if txn else None)
        transaction_type = txn.transaction_type if txn else "CREDIT"

        return PaymentIntakeContext(
            payment_id=payment.id,
            company_id=payment.company_id,
            amount=Decimal(str(payment.amount)),
            currency=payment.currency,
            payment_date=payment.transaction_date,
            payer_raw_name=payer_raw_name,
            payer_raw_identifier=payment.payer_raw_identifier,
            payment_reference=payment_reference,
            bank_account_number=bank_account_number,
            narration=narration,
            bank_transaction_id=payment.bank_transaction_id,
            allocated_amount=Decimal(str(payment.allocated_amount)),
            unallocated_amount=Decimal(str(payment.unallocated_amount)),
            status=payment.status,
            transaction_type=transaction_type,
        )

    def list_unreconciled_payment_contexts(
        self, company_id: UUID, limit: int = 50
    ) -> List[PaymentIntakeContext]:
        """Fetch unreconciled payment intake contexts for tenant."""
        payments = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.company_id == company_id,
                PaymentModel.status == "UNRECONCILED",
            )
            .order_by(
                PaymentModel.transaction_date.desc(),
                PaymentModel.created_at.desc(),
                PaymentModel.id.asc(),
            )
            .limit(limit)
            .all()
        )

        return [
            PaymentIntakeContext(
                payment_id=p.id,
                company_id=p.company_id,
                amount=Decimal(str(p.amount)),
                currency=p.currency,
                payment_date=p.transaction_date,
                payer_raw_name=p.payer_raw_name or (p.bank_transaction.counterparty_name if p.bank_transaction else None),
                payer_raw_identifier=p.payer_raw_identifier,
                payment_reference=p.reference_number or (p.bank_transaction.reference_number if p.bank_transaction else None),
                bank_account_number=p.bank_account_number or (p.bank_transaction.bank_account_number if p.bank_transaction else None),
                narration=p.narration or (p.bank_transaction.narration if p.bank_transaction else None),
                bank_transaction_id=p.bank_transaction_id,
                allocated_amount=Decimal(str(p.allocated_amount)),
                unallocated_amount=Decimal(str(p.unallocated_amount)),
                status=p.status,
                transaction_type=p.bank_transaction.transaction_type if p.bank_transaction else "CREDIT",
            )
            for p in payments
        ]

    def list_eligible_intake_contexts(
        self, company_id: UUID, limit: int = 50
    ) -> List[PaymentIntakeContext]:
        """Fetch eligible (UNRECONCILED / PARTIALLY_RECONCILED with balance > 0) payment contexts."""
        payments = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.company_id == company_id,
                PaymentModel.status.in_(["UNRECONCILED", "PARTIALLY_RECONCILED"]),
                PaymentModel.unallocated_amount > Decimal("0.00"),
            )
            .order_by(
                PaymentModel.transaction_date.desc(),
                PaymentModel.created_at.desc(),
                PaymentModel.id.asc(),
            )
            .limit(limit)
            .all()
        )

        return [
            PaymentIntakeContext(
                payment_id=p.id,
                company_id=p.company_id,
                amount=Decimal(str(p.amount)),
                currency=p.currency,
                payment_date=p.transaction_date,
                payer_raw_name=p.payer_raw_name or (p.bank_transaction.counterparty_name if p.bank_transaction else None),
                payer_raw_identifier=p.payer_raw_identifier,
                payment_reference=p.reference_number or (p.bank_transaction.reference_number if p.bank_transaction else None),
                bank_account_number=p.bank_account_number or (p.bank_transaction.bank_account_number if p.bank_transaction else None),
                narration=p.narration or (p.bank_transaction.narration if p.bank_transaction else None),
                bank_transaction_id=p.bank_transaction_id,
                allocated_amount=Decimal(str(p.allocated_amount)),
                unallocated_amount=Decimal(str(p.unallocated_amount)),
                status=p.status,
                transaction_type=p.bank_transaction.transaction_type if p.bank_transaction else "CREDIT",
            )
            for p in payments
        ]


class SQLAlchemyInvoiceLookupAdapter(InvoiceLookupPort):
    """Database adapter retrieving tenant-isolated open candidate invoices."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_customer_candidate_invoices(
        self,
        company_id: UUID,
        customer_id: UUID,
        currency: str,
    ) -> List[InvoiceCandidateContext]:
        """Fetch all unarchived, open invoices for a customer matching payment currency."""
        invoices = (
            self.db.query(InvoiceModel)
            .filter(
                InvoiceModel.company_id == company_id,
                InvoiceModel.customer_id == customer_id,
                InvoiceModel.is_archived.is_(False),
                InvoiceModel.status.in_([
                    InvoiceStatus.PENDING.value,
                    InvoiceStatus.PARTIALLY_PAID.value,
                ]),
                InvoiceModel.outstanding_amount > 0,
                InvoiceModel.currency == currency,
            )
            .order_by(
                InvoiceModel.due_date.asc(),
                InvoiceModel.issue_date.asc(),
                InvoiceModel.invoice_number.asc(),
                InvoiceModel.id.asc(),
            )
            .all()
        )

        return [
            InvoiceCandidateContext(
                id=inv.id,
                company_id=inv.company_id,
                customer_id=inv.customer_id,
                invoice_number=inv.invoice_number,
                issue_date=inv.issue_date,
                due_date=inv.due_date,
                total_amount=Decimal(str(inv.total_amount)),
                paid_amount=Decimal(str(inv.paid_amount)),
                outstanding_amount=Decimal(str(inv.outstanding_amount)),
                currency=inv.currency,
                status=inv.status,
                is_archived=inv.is_archived,
            )
            for inv in invoices
        ]

    def count_customer_invoices_in_other_currencies(
        self,
        company_id: UUID,
        customer_id: UUID,
        payment_currency: str,
    ) -> int:
        """Count open invoices for customer in other currencies to detect currency mismatch."""
        count = (
            self.db.query(func.count(InvoiceModel.id))
            .filter(
                InvoiceModel.company_id == company_id,
                InvoiceModel.customer_id == customer_id,
                InvoiceModel.is_archived.is_(False),
                InvoiceModel.status.in_([
                    InvoiceStatus.PENDING.value,
                    InvoiceStatus.PARTIALLY_PAID.value,
                ]),
                InvoiceModel.outstanding_amount > 0,
                InvoiceModel.currency != payment_currency,
            )
            .scalar()
        )
        return int(count or 0)


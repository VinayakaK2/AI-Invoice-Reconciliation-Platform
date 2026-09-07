"""Payment and bank statement module presentation router.

Exposes REST API endpoints for bank statement ingestion, raw bank transactions,
normalized payments, manual payment creation, and accountant ignore/unignore workflows.
Adheres to the universal {"success": True, "data": ...} envelope standard.
"""

from datetime import date
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.domain.entities import User
from app.modules.auth.presentation.dependencies import get_current_user
from app.modules.payment.application.use_cases import (
    CreateManualPaymentUseCase,
    GetImportBatchDetailUseCase,
    GetPaymentDetailUseCase,
    IgnorePaymentUseCase,
    ImportBankStatementCSVUseCase,
    ListBankTransactionsUseCase,
    ListImportBatchesUseCase,
    ListPaymentsUseCase,
    UnignorePaymentUseCase,
)
from app.modules.payment.presentation.schemas import (
    BankTransactionListResponse,
    BankTransactionResponse,
    CSVRowErrorResponse,
    CreateManualPaymentRequest,
    IgnorePaymentRequest,
    ImportBatchDetailResponse,
    ImportBatchResponse,
    ImportStatementResponse,
    PaymentListResponse,
    PaymentResponse,
)

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("/status")
def payment_module_status() -> dict:
    """Return payment module readiness status."""
    return {"module": "payment", "status": "initialized"}


@router.post("/upload-statement", status_code=status.HTTP_201_CREATED)
async def upload_bank_statement(
    file: UploadFile = File(...),
    bank_account_number: Optional[str] = Form(None),
    default_currency: str = Form("INR"),
    skip_duplicates: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Upload and ingest bank statement CSV file."""
    content = await file.read()

    use_case = ImportBankStatementCSVUseCase(db=db)
    result = use_case.execute(
        company_id=current_user.company_id,
        file_content=content,
        file_name=file.filename or "statement.csv",
        bank_account_number=bank_account_number,
        default_currency=default_currency,
        skip_duplicates=skip_duplicates,
    )

    return {
        "success": True,
        "data": ImportStatementResponse(
            batch_id=result.batch_id,
            file_name=result.file_name,
            total_rows=result.total_rows,
            imported_payments_count=result.imported_payments_count,
            skipped_debits_count=result.skipped_debits_count,
            skipped_duplicates_count=result.skipped_duplicates_count,
            failed_rows_count=result.failed_rows_count,
            errors=[
                CSVRowErrorResponse(
                    row_number=e.row_number,
                    field=e.field,
                    message=e.message,
                )
                for e in result.errors
            ],
            imported_payment_ids=result.imported_payment_ids,
        ),
    }


@router.get("/batches", response_model=Dict[str, Any])
def list_import_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List statement import batches for current company context."""
    use_case = ListImportBatchesUseCase(db=db)
    batches, total = use_case.execute(
        company_id=current_user.company_id,
        page=page,
        page_size=page_size,
    )

    items = [
        ImportBatchResponse(
            id=b.id,
            company_id=b.company_id,
            file_name=b.file_name,
            file_hash=b.file_hash,
            total_rows=b.total_rows,
            imported_count=b.imported_count,
            failed_count=b.failed_count,
            duplicate_count=b.duplicate_count,
            status=b.status.value if hasattr(b.status, "value") else b.status,
            error_message=b.error_message,
            created_at=b.created_at or date.today(),
        )
        for b in batches
    ]

    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "success": True,
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
    }


@router.get("/batches/{batch_id}", response_model=Dict[str, Any])
def get_import_batch_detail(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Fetch statement batch detail and its raw transactions."""
    use_case = GetImportBatchDetailUseCase(db=db)
    batch, transactions = use_case.execute(
        batch_id=batch_id,
        company_id=current_user.company_id,
    )

    batch_resp = ImportBatchResponse(
        id=batch.id,
        company_id=batch.company_id,
        file_name=batch.file_name,
        file_hash=batch.file_hash,
        total_rows=batch.total_rows,
        imported_count=batch.imported_count,
        failed_count=batch.failed_count,
        duplicate_count=batch.duplicate_count,
        status=batch.status.value if hasattr(batch.status, "value") else batch.status,
        error_message=batch.error_message,
        created_at=batch.created_at or date.today(),
    )

    txn_resps = [BankTransactionResponse.from_domain(t) for t in transactions]

    return {
        "success": True,
        "data": ImportBatchDetailResponse(
            batch=batch_resp,
            transactions=txn_resps,
        ),
    }


@router.get("/transactions", response_model=Dict[str, Any])
def list_bank_transactions(
    txn_type: Optional[str] = Query(None, description="CREDIT or DEBIT"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List raw bank statement transactions (both credits and debits)."""
    use_case = ListBankTransactionsUseCase(db=db)
    transactions, total = use_case.execute(
        company_id=current_user.company_id,
        txn_type=txn_type,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )

    items = [BankTransactionResponse.from_domain(t) for t in transactions]
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    return {
        "success": True,
        "data": BankTransactionListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        ),
    }


@router.get("", response_model=Dict[str, Any])
def list_payments(
    status: Optional[str] = Query(None, description="UNRECONCILED, PARTIALLY_RECONCILED, RECONCILED, IGNORED"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    min_amount: Optional[Decimal] = Query(None),
    max_amount: Optional[Decimal] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List normalized incoming receivable payments with filtering and pagination."""
    use_case = ListPaymentsUseCase(db=db)
    payments, total = use_case.execute(
        company_id=current_user.company_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        min_amount=min_amount,
        max_amount=max_amount,
        search=search,
        page=page,
        page_size=page_size,
    )

    items = [PaymentResponse.from_domain(p) for p in payments]
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    return {
        "success": True,
        "data": PaymentListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Dict[str, Any])
def create_manual_payment(
    req: CreateManualPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create a manual incoming bank receipt payment."""
    use_case = CreateManualPaymentUseCase(db=db)
    payment = use_case.execute(
        company_id=current_user.company_id,
        transaction_date=req.transaction_date,
        amount=req.amount,
        currency=req.currency,
        narration=req.narration,
        reference_number=req.reference_number,
        bank_account_number=req.bank_account_number,
        payer_raw_name=req.payer_raw_name,
        notes=req.notes,
    )

    return {
        "success": True,
        "data": PaymentResponse.from_domain(payment),
    }


@router.get("/{payment_id}", response_model=Dict[str, Any])
def get_payment_detail(
    payment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Fetch single payment details strictly within tenant boundary."""
    use_case = GetPaymentDetailUseCase(db=db)
    payment = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
    )

    return {
        "success": True,
        "data": PaymentResponse.from_domain(payment),
    }


@router.post("/{payment_id}/ignore", response_model=Dict[str, Any])
def ignore_payment(
    payment_id: UUID,
    req: IgnorePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Mark payment as IGNORED by accountant."""
    use_case = IgnorePaymentUseCase(db=db)
    payment = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        reason=req.reason,
    )

    return {
        "success": True,
        "data": PaymentResponse.from_domain(payment),
    }


@router.post("/{payment_id}/unignore", response_model=Dict[str, Any])
def unignore_payment(
    payment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Restore an IGNORED payment back to UNRECONCILED."""
    use_case = UnignorePaymentUseCase(db=db)
    payment = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
    )

    return {
        "success": True,
        "data": PaymentResponse.from_domain(payment),
    }

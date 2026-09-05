"""B8 read endpoints; Phase 4's send endpoint and computation stay unchanged."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import PAYROLL_ROLES
from app.services.payslip_delivery import delivery_status
from app.services.payslip_documents import document_html, document_pdf

router = APIRouter(
    tags=["Payslip documents"],
    dependencies=[
        Depends(require_role(PAYROLL_ROLES)),
        Depends(rate_limiter),
    ],
)

PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/payslips/{public_id}/preview", response_class=HTMLResponse)
async def preview(public_id: str, db: AsyncSession = Depends(get_db)):
    return HTMLResponse(
        await document_html(db, public_id),
        headers={
            **PRIVATE_HEADERS,
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; font-src data:; frame-ancestors 'self'",
        },
    )


@router.get("/payslips/{public_id}/pdf")
async def print_payslip(public_id: str, db: AsyncSession = Depends(get_db)):
    return Response(
        await document_pdf(db, public_id),
        media_type="application/pdf",
        headers={
            **PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="payslip-{public_id}.pdf"',
        },
    )


@router.get("/payruns/{public_id}/deliveries")
async def deliveries(public_id: str, db: AsyncSession = Depends(get_db)):
    return {"items": await delivery_status(db, public_id)}

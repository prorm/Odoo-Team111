"""Per-employee delivery, separate from Phase 4 computation and snapshots.

Dispatcher commits pending rows before enqueueing children. Repeated bulk
requests skip sent rows; pending/failed rows can be queued again. A row lock
serializes duplicate children through SMTP and the result commit. SMTP itself
has no exactly-once protocol: a worker crash after SMTP acceptance but before
commit can cause a duplicate on explicit retry. A crashed pending job can be
requeued with Send payslips. 'sent' means SMTP accepted, not inbox delivery.
"""

import asyncio
import logging
from datetime import UTC, datetime
from email.message import EmailMessage

import aiosmtplib
from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.locks import acquire_entity_lock
from app.models.enums import PayrunStatus, PayslipStatus
from app.models.payroll import Payslip
from app.models.payslip_delivery import PayslipDelivery
from app.services.payroll import PayrunService, PayslipService
from app.services.payslip_documents import document_pdf
from app.services.payslip_snapshot import payslip_response

logger = logging.getLogger(__name__)


async def dispatch_deliveries(payload: dict, enqueue) -> dict:
    """Consume Phase 4's unchanged send_payslips dict; fan out one job per slip."""
    queued = []
    async with AsyncSessionLocal() as session:
        await acquire_entity_lock(
            session, "payslip_delivery_dispatch", payload["payrun_id"]
        )
        run = await PayrunService(session).get_or_404(payload["payrun_id"])
        if run.status != PayrunStatus.PAID:
            raise HTTPException(409, "Only paid payruns can be emailed.")
        for item in payload["payslips"]:
            slip = (
                await session.execute(
                    select(Payslip).where(
                        Payslip.public_id == item["payslip_id"],
                        Payslip.payrun_id == run.id,
                        Payslip.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if slip is None:
                # A stale/mismatched item must not abort other employees.
                logger.error(
                    "Delivery item does not belong to payrun %s", run.public_id
                )
                continue
            delivery = (
                await session.execute(
                    select(PayslipDelivery)
                    .where(PayslipDelivery.payslip_id == slip.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if delivery and delivery.status == "sent":
                continue
            if delivery is None:
                delivery = PayslipDelivery(payslip_id=slip.id, payrun_id=run.id)
                session.add(delivery)
            delivery.payload = {
                **{
                    key: payload[key]
                    for key in (
                        "payrun_id",
                        "payrun_name",
                        "period_start",
                        "period_end",
                    )
                },
                "payslips": [dict(item)],
            }
            delivery.status, delivery.error = "pending", None
            delivery.queued_at = datetime.now(UTC)
            await session.flush()
            queued.append(delivery.id)
        await session.commit()

    for delivery_id in queued:
        try:
            await enqueue(delivery_id)
        except Exception:
            logger.exception("Could not queue payslip delivery %s", delivery_id)
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        select(PayslipDelivery)
                        .where(PayslipDelivery.id == delivery_id)
                        .with_for_update()
                    )
                ).scalar_one()
                if row.status == "pending":
                    row.status, row.error = (
                        "failed",
                        "Could not queue delivery. Use Send payslips to retry.",
                    )
                    await session.commit()
    return {"payrun_id": payload["payrun_id"], "delivery_count": len(queued)}


async def deliver_one(delivery_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        delivery = (
            await session.execute(
                select(PayslipDelivery)
                .where(PayslipDelivery.id == delivery_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if delivery is None:
            return {"status": "failed", "error": "Delivery not found"}
        if delivery.status != "pending":
            return {"status": delivery.status}
        delivery.attempts += 1
        try:
            payload = delivery.payload
            item = payload["payslips"][0]
            row = await PayslipService(session).read_payslip(item["payslip_id"])
            if (
                row.status != PayslipStatus.PAID
                or row.payrun.status != PayrunStatus.PAID
            ):
                raise HTTPException(409, "Only paid payslips can be emailed.")
            snapshot = payslip_response(row)
            expected = {
                "payslip_id": snapshot.id,
                "employee_id": snapshot.employee.id,
                "work_email": snapshot.employee.work_email,
                "net_amount": str(snapshot.net_amount),
                "gross_amount": str(snapshot.gross_amount),
            }
            if item != expected or payload["payrun_id"] != snapshot.payrun.id:
                raise HTTPException(
                    409, "Delivery payload does not match the stored payslip."
                )
            recipient = validate_email(
                item["work_email"], check_deliverability=False, allow_smtputf8=False
            ).normalized
            pdf = await document_pdf(session, snapshot.id)
            message = EmailMessage()
            message["From"] = settings.SMTP_FROM_ADDRESS
            message["To"] = recipient
            message["Subject"] = (
                f"Payslip: {snapshot.payrun.period_start} to {snapshot.payrun.period_end}"
            )
            message["Message-ID"] = f"<{delivery.id}@peoplepay360.local>"
            message.set_content(
                f"Hello {snapshot.employee.first_name},\n\nYour payslip is attached.\n\nPeoplePay360"
            )
            message.add_attachment(
                pdf,
                maintype="application",
                subtype="pdf",
                filename=f"payslip-{snapshot.id}.pdf",
            )
            await asyncio.wait_for(
                aiosmtplib.send(
                    message,
                    hostname=settings.SMTP_HOST,
                    port=settings.SMTP_PORT,
                    username=settings.SMTP_USERNAME or None,
                    password=settings.SMTP_PASSWORD or None,
                    start_tls=settings.SMTP_STARTTLS,
                    timeout=30,
                ),
                timeout=45,
            )
            delivery.status, delivery.error = "sent", None
            delivery.sent_at = datetime.now(UTC)
        except Exception as exc:
            logger.exception("Payslip delivery %s failed", delivery_id)
            delivery.status = "failed"
            if isinstance(exc, EmailNotValidError):
                delivery.error = "Invalid or unsupported recipient email address."
            elif isinstance(exc, HTTPException):
                delivery.error = str(
                    exc.detail.get("message", exc.detail)
                    if isinstance(exc.detail, dict)
                    else exc.detail
                )
            elif isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
                delivery.error = "SMTP rejected the recipient email address."
            else:
                delivery.error = "Could not render or send this payslip. Check SMTP/worker logs, then retry."
        await session.commit()
        return {"status": delivery.status, "error": delivery.error}


async def delivery_status(session, payrun_id: str) -> list[dict]:
    run = await PayrunService(session).get_or_404(payrun_id)
    rows = (
        await session.execute(
            select(Payslip, PayslipDelivery)
            .outerjoin(PayslipDelivery, PayslipDelivery.payslip_id == Payslip.id)
            .where(Payslip.payrun_id == run.id, Payslip.deleted_at.is_(None))
            .order_by(Payslip.id)
        )
    ).all()
    return [
        {
            "payslip_id": slip.public_id,
            "status": delivery.status if delivery else "pending",
            "queued": delivery is not None,
            "error": delivery.error if delivery else None,
            "attempts": delivery.attempts if delivery else 0,
            "sent_at": delivery.sent_at if delivery else None,
        }
        for slip, delivery in rows
    ]

"""Phase 4 publishes send_payslips; this module supplies its consumer."""

from app.jobs.broker import broker
from app.services.payslip_delivery import deliver_one, dispatch_deliveries


@broker.task(task_name="deliver_payslip")
async def deliver_payslip(delivery_id: str) -> dict:
    return await deliver_one(delivery_id)


@broker.task(task_name="send_payslips")
async def send_payslips(payload: dict) -> dict:
    return await dispatch_deliveries(payload, deliver_payslip.kiq)

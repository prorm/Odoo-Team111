"""B8: real PDFs, real SMTP, immutable history and independent deliveries."""

import asyncio
from datetime import date
from email import policy
from email.parser import BytesParser
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from aiosmtpd.controller import Controller
from pypdf import PdfReader
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.enums import UserRole
from app.models.payroll import Payslip
from app.seed import _seed_salary_structure
from app.services.payslip_delivery import deliver_one, dispatch_deliveries
from app.services.payslip_documents import html_to_pdf
from tests.conftest import auth_headers
from tests.test_payroll_api import (
    HR_MANAGER,
    PAYROLL_USER,
    attach_working_schedule,
    compute,
    make_attendance,
    make_contract,
    make_employee,
    make_payroll_leave,
    make_payrun,
)
from tests.test_payroll_gaps import slips, transition


@pytest_asyncio.fixture
async def document_run(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    employees, contracts = [], []
    for name in ("Asha", "Bela", "Invalid"):
        employee = await make_employee(client, first_name=name)
        await attach_working_schedule(client, employee["id"])
        contracts.append(await make_contract(client, employee["id"], wage="30000.00"))
        await make_attendance(client, employee["id"], date(2025, 3, 3))
        await make_payroll_leave(
            client, employee["id"], date(2025, 3, 10), date(2025, 3, 12)
        )
        employees.append(employee)
    async with AsyncSessionLocal() as session:
        structure = await _seed_salary_structure(session)
        await session.commit()
        structure_id = structure.public_id
    run = await make_payrun(client, structure_id, [emp["id"] for emp in employees])
    await compute(client, run)
    return run, await slips(client, run), employees, contracts


def pdf_text(content):
    # Layout extraction respects kerning (plain extraction splits "Tax" as
    # "T ax" in DejaVu Sans), without relaxing any rule-label assertion.
    return "\n".join(
        page.extract_text(extraction_mode="layout")
        for page in PdfReader(BytesIO(content)).pages
    )


async def queued_payload(client, run):
    with patch(
        "app.services.payroll.PayrunService._kick_delivery",
        new_callable=AsyncMock,
        return_value="b8-test",
    ) as queue:
        response = await client.post(
            f"/api/v1/payruns/{run['id']}/send-payslips", headers=PAYROLL_USER
        )
        assert response.status_code == 202, response.text
        return queue.call_args.args[0]


async def test_shared_preview_pdf_lop_warning_exclusion_and_historical_bytes(
    client, document_run
):
    run, rows, employees, contracts = document_run
    public_id = next(
        row["id"] for row in rows if row["employee"]["id"] == employees[0]["id"]
    )
    url = f"/api/v1/payslips/{public_id}"
    preview = await client.get(url + "/preview", headers=PAYROLL_USER)
    assert preview.status_code == 200, preview.text
    assert (await client.get(url + "/pdf", headers=PAYROLL_USER)).status_code == 409
    await transition(client, run, "validate")
    # An advisory is allowed on a validated slip; its content must never print.
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(Payslip).where(Payslip.public_id == public_id))
        ).scalar_one()
        row.warnings = [
            {
                "code": "structure_mismatch",
                "severity": "advisory",
                "message": "INTERNAL_WARNING_DO_NOT_PRINT",
                "references": [],
            }
        ]
        await session.commit()
    preview = await client.get(url + "/preview", headers=PAYROLL_USER)
    pdf = await client.get(url + "/pdf", headers=PAYROLL_USER)
    assert pdf.status_code == 200, pdf.text[:200] if pdf.status_code != 200 else ""
    assert pdf.headers["content-type"] == "application/pdf"
    assert "attachment" in pdf.headers["content-disposition"]
    assert pdf.headers["cache-control"] == "no-store"
    assert pdf.content == html_to_pdf(preview.text)
    text = pdf_text(pdf.content)
    positions = [text.index(line["code"]) for line in rows[0]["lines"]]
    assert positions == sorted(positions)
    for line in rows[0]["lines"]:
        assert f'data-line-code="{line["code"]}"' in preview.text
        assert line["name"] in " ".join(text.split())
    assert "Loss of Pay" in text and "4,285.71" in text and "37,514.29" in text
    assert "INTERNAL_WARNING_DO_NOT_PRINT" not in preview.text + text
    await transition(client, run, "mark-paid")
    changed_contract = await client.patch(
        f"/api/v1/contracts/{contracts[0]['id']}",
        headers=HR_MANAGER,
        json={"wage": "99000.00"},
    )
    assert changed_contract.status_code == 200, changed_contract.text
    assert changed_contract.json()["wage"] == "99000.00"
    changed_employee = await client.patch(
        f"/api/v1/employees/{employees[0]['id']}",
        headers=HR_MANAGER,
        json={"first_name": "Changed"},
    )
    assert changed_employee.status_code == 200, changed_employee.text
    assert changed_employee.json()["first_name"] == "Changed"
    assert (
        await client.get(url + "/preview", headers=PAYROLL_USER)
    ).content == preview.content
    assert (await client.get(url + "/pdf", headers=PAYROLL_USER)).content == pdf.content


@pytest.mark.parametrize("role", list(UserRole))
async def test_document_and_delivery_status_rbac(client, document_run, role):
    run, rows, *_ = document_run
    await transition(client, run, "validate")
    expected = 403 if role in (UserRole.EMPLOYEE, UserRole.HR_MANAGER) else 200
    for url in (
        f"/api/v1/payslips/{rows[0]['id']}/preview",
        f"/api/v1/payslips/{rows[0]['id']}/pdf",
        f"/api/v1/payruns/{run['id']}/deliveries",
    ):
        assert (
            await client.get(url, headers=auth_headers(role))
        ).status_code == expected


async def test_legacy_document_fails_clearly_without_live_backfill(
    client, document_run
):
    run, rows, *_ = document_run
    await transition(client, run, "validate")
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == rows[0]["id"])
            )
        ).scalar_one()
        row.reference_snapshot = None
        await session.commit()
    for action in ("preview", "pdf"):
        result = await client.get(
            f"/api/v1/payslips/{rows[0]['id']}/{action}", headers=PAYROLL_USER
        )
        assert result.status_code == 409
        assert result.json()["detail"]["code"] == "historical_snapshot_unavailable"


@pytest.fixture
def smtp_server(monkeypatch, unused_tcp_port):
    class Handler:
        messages = []
        reject = None

        async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
            if address == self.reject:
                return "550 5.1.1 No such mailbox"
            envelope.rcpt_tos.append(address)
            return "250 OK"

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(
                BytesParser(policy=policy.default).parsebytes(envelope.content)
            )
            return "250 Accepted"

    handler = Handler()
    controller = Controller(handler, hostname="127.0.0.1", port=unused_tcp_port)
    controller.start()
    monkeypatch.setattr(settings, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SMTP_PORT", unused_tcp_port)
    yield handler
    controller.stop()


async def test_bulk_real_smtp_two_sent_one_rejected_then_retry_without_duplicates(
    client, document_run, smtp_server
):
    run, rows, employees, *_ = document_run
    await transition(client, run, "validate")
    await transition(client, run, "mark-paid")
    smtp_server.reject = employees[2]["work_email"]
    payload = await queued_payload(client, run)
    assert set(payload) == {
        "payrun_id",
        "payrun_name",
        "period_start",
        "period_end",
        "payslips",
    }
    queue = AsyncMock()
    await dispatch_deliveries(payload, queue)
    ids = [call.args[0] for call in queue.call_args_list]
    assert len(ids) == 3
    statuses = (
        await client.get(
            f"/api/v1/payruns/{run['id']}/deliveries", headers=PAYROLL_USER
        )
    ).json()["items"]
    assert all(row["status"] == "pending" and row["queued"] for row in statuses)
    # Duplicate queue execution also must not send the first employee twice.
    await asyncio.gather(*(deliver_one(id) for id in ids), deliver_one(ids[0]))
    statuses = (
        await client.get(
            f"/api/v1/payruns/{run['id']}/deliveries", headers=PAYROLL_USER
        )
    ).json()["items"]
    assert sorted(row["status"] for row in statuses) == ["failed", "sent", "sent"]
    assert "SMTP rejected" in next(
        row["error"] for row in statuses if row["status"] == "failed"
    )
    assert len(smtp_server.messages) == 2
    for message in smtp_server.messages:
        attachments = list(message.iter_attachments())
        assert (
            len(attachments) == 1
            and attachments[0].get_content_type() == "application/pdf"
        )
        assert "4,285.71" in pdf_text(attachments[0].get_payload(decode=True))
    smtp_server.reject = None
    queue.reset_mock()
    await dispatch_deliveries(payload, queue)
    assert queue.call_count == 1
    await deliver_one(queue.call_args.args[0])
    assert len(smtp_server.messages) == 3


async def test_child_enqueue_failure_does_not_block_other_employees(
    client, document_run
):
    run, *_ = document_run
    await transition(client, run, "validate")
    await transition(client, run, "mark-paid")
    queue = AsyncMock(side_effect=[RuntimeError("broker interrupted"), None, None])
    await dispatch_deliveries(await queued_payload(client, run), queue)
    assert queue.call_count == 3
    result = (
        await client.get(
            f"/api/v1/payruns/{run['id']}/deliveries", headers=PAYROLL_USER
        )
    ).json()
    assert sorted(row["status"] for row in result["items"]) == [
        "failed",
        "pending",
        "pending",
    ]


async def test_dispatcher_refuses_unpaid_run(client, document_run):
    from fastapi import HTTPException

    run, *_ = document_run
    with pytest.raises(HTTPException) as failure:
        await dispatch_deliveries({"payrun_id": run["id"], "payslips": []}, AsyncMock())
    assert failure.value.status_code == 409

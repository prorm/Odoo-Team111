"""Opt-in local B8 acceptance fixture, never run by application startup.

Run with dev dependencies in a dedicated demo database:
  python -m scripts.verify_payslip_delivery prepare
  python -m scripts.verify_payslip_delivery smtp-proxy

The proxy rejects one nonexistent test mailbox with SMTP 550 and forwards
the two valid recipients to MailHog. MailHog normally accepts every mailbox,
so this explicit fault injection exercises a real rejection, not a mock or
an assertion that MailHog validates addresses. Configure the demo worker's
SMTP_HOST=127.0.0.1 SMTP_PORT=1026. Use Send payslips in the UI, then run:
  python -m scripts.verify_payslip_delivery verify

prepare retains three paid LOP payslips and writes artifacts/phase5-fixture.json.
It also generates the actual HTML/PDF for visual inspection. No real mail
server is contacted; the proxy only forwards to MailHog on port 1025.
"""

import asyncio
import json
import os
import sys
from email import policy
from email.parser import Parser
from io import BytesIO
from datetime import date
from pathlib import Path
from uuid import uuid4

import aiosmtplib
from aiosmtpd.controller import Controller
from httpx import AsyncClient
from pypdf import PdfReader

from app.core.database import AsyncSessionLocal
from app.seed import _seed_salary_structure
from tests.test_payroll_api import (
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

ARTIFACTS = Path("artifacts")
FIXTURE = ARTIFACTS / "phase5-fixture.json"
API = os.environ.get("VERIFY_API_URL", "http://127.0.0.1:8000")


async def prepare():
    suffix = uuid4().hex[:8]
    async with AsyncClient(base_url=API, timeout=60) as client:
        employees = []
        for name in ("Asha", "Bela", "Invalid"):
            employee = await make_employee(
                client,
                first_name=name,
                last_name="Delivery Demo",
                work_email=f"phase5.{name.lower()}.{suffix}@example.com",
            )
            await attach_working_schedule(client, employee["id"])
            await make_contract(client, employee["id"], wage="30000.00")
            await make_attendance(client, employee["id"], date(2025, 3, 3))
            await make_payroll_leave(
                client, employee["id"], date(2025, 3, 10), date(2025, 3, 12)
            )
            employees.append(employee)
        async with AsyncSessionLocal() as session:
            structure = await _seed_salary_structure(session)
            await session.commit()
            structure_id = structure.public_id
        run = await make_payrun(
            client,
            structure_id,
            [emp["id"] for emp in employees],
            name="March 2025 · Delivery verification",
        )
        await compute(client, run)
        await transition(client, run, "validate")
        await transition(client, run, "mark-paid")
        rows = await slips(client, run)
        slip = next(row for row in rows if row["employee"]["id"] == employees[0]["id"])
        ARTIFACTS.mkdir(exist_ok=True)
        for action, filename in (
            ("preview", "phase5-preview.html"),
            ("pdf", "phase5-sample.pdf"),
        ):
            result = await client.get(
                f"/api/v1/payslips/{slip['id']}/{action}", headers=PAYROLL_USER
            )
            result.raise_for_status()
            (ARTIFACTS / filename).write_bytes(result.content)
        fixture = {
            "payrun_id": run["id"],
            "payslip_id": slip["id"],
            "recipients": [emp["work_email"] for emp in employees],
            "reject": employees[2]["work_email"],
        }
        FIXTURE.write_text(json.dumps(fixture, indent=2))
        print(json.dumps(fixture))


async def smtp_proxy():
    fixture = json.loads(FIXTURE.read_text())

    class Handler:
        async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
            if address == fixture["reject"]:
                return "550 5.1.1 No such mailbox (Phase 5 acceptance fixture)"
            envelope.rcpt_tos.append(address)
            return "250 OK"

        async def handle_DATA(self, server, session, envelope):
            await aiosmtplib.send(
                envelope.content,
                sender=envelope.mail_from,
                recipients=envelope.rcpt_tos,
                hostname=os.environ.get("MAILHOG_HOST", "host.docker.internal"),
                port=1025,
                start_tls=False,
            )
            return "250 Forwarded to MailHog"

    controller = Controller(Handler(), hostname="127.0.0.1", port=1026)
    controller.start()
    try:
        print("SMTP fixture listening on 1026; only MailHog receives mail", flush=True)
        await asyncio.Event().wait()
    finally:
        controller.stop()


async def verify():
    fixture = json.loads(FIXTURE.read_text())
    async with AsyncClient(base_url=API, timeout=60) as client:
        result = await client.get(
            f"/api/v1/payruns/{fixture['payrun_id']}/deliveries", headers=PAYROLL_USER
        )
        result.raise_for_status()
        rows = result.json()["items"]
        assert sorted(row["status"] for row in rows) == ["failed", "sent", "sent"], rows
        print(json.dumps(result.json(), indent=2))
        mailbox = await client.get(
            os.environ.get("MAILHOG_URL", "http://host.docker.internal:8025")
            + "/api/v2/messages"
        )
        mailbox.raise_for_status()
        accepted = []
        for item in mailbox.json()["items"]:
            message = Parser(policy=policy.default).parsestr(item["Raw"]["Data"])
            if str(message["To"]) not in fixture["recipients"]:
                continue
            assert str(message["To"]) != fixture["reject"]
            attachments = list(message.iter_attachments())
            assert len(attachments) == 1
            pdf = attachments[0].get_payload(decode=True)
            text = " ".join(
                page.extract_text() for page in PdfReader(BytesIO(pdf)).pages
            )
            assert "PP360_LOP" in text and "4,285.71" in text and "37,514.29" in text
            accepted.append(str(message["To"]))
        assert len(accepted) == 2, accepted
        print(
            "MailHog: exactly 2 accepted recipients, each with a real LOP PDF attachment"
        )


if __name__ == "__main__":
    asyncio.run(
        {"prepare": prepare, "smtp-proxy": smtp_proxy, "verify": verify}[sys.argv[1]]()
    )

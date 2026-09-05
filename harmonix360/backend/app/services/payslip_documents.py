"""One template for preview, download and email; all data is persisted history.

Preview also supports computed slips for the existing View calculation flow.
PDF/download is restricted to validated/paid. Warnings stay in the surrounding
application UI and are never passed to the document template. Rendering does
not call the salary resolver, sum money, or access current contract inputs.
"""

import asyncio
import base64
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from app.core.config import settings

from app.models.enums import PayslipStatus
from app.services.payroll import PayslipService
from app.services.payslip_snapshot import payslip_response

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
environment = Environment(
    loader=FileSystemLoader(TEMPLATES),
    undefined=StrictUndefined,
    autoescape=select_autoescape(default=True),
)
environment.filters["money"] = lambda amount: format(amount, ",.2f")


@lru_cache(maxsize=1)
def document_fonts():
    """Embed the same installed fonts so browser/OS substitutions cannot drift."""
    directory = Path(settings.PAYSLIP_FONT_DIR)
    return {
        name: base64.b64encode((directory / filename).read_bytes()).decode("ascii")
        for name, filename in (
            ("font_regular", "DejaVuSans.ttf"),
            ("font_bold", "DejaVuSans-Bold.ttf"),
        )
    }


async def document_html(session, public_id: str, *, printable: bool = False) -> str:
    row = await PayslipService(session).read_payslip(public_id)
    if row.payrun.deleted_at is not None:
        raise HTTPException(404, "Payrun not found")
    if printable and row.status not in (PayslipStatus.VALIDATED, PayslipStatus.PAID):
        raise HTTPException(409, "Only validated or paid payslips can be printed.")
    snapshot = payslip_response(row)
    # Exclude warnings from the data boundary, not merely with CSS hiding.
    data = snapshot.model_dump(exclude={"warnings"})
    return environment.get_template("payslip.html.j2").render(
        slip=data, **document_fonts()
    )


def _no_external_resources(url, *args, **kwargs):
    from weasyprint.urls import URLFetcherResponse

    if url.startswith("data:font/ttf;base64,"):
        return URLFetcherResponse(
            url,
            body=base64.b64decode(url.split(",", 1)[1], validate=True),
            headers={"Content-Type": "font/ttf"},
        )
    raise ValueError("Payslip documents do not load external resources")


def html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html, url_fetcher=_no_external_resources).write_pdf()


async def document_pdf(session, public_id: str) -> bytes:
    html = await document_html(session, public_id, printable=True)
    return await asyncio.to_thread(html_to_pdf, html)

# High-Fidelity Data Export & Reporting Engine

## Why this matters
Every ERP system lives or dies on reporting — judges evaluating "production-ready systems that can handle complex business logic" will notice if your only output is a JSON API. Branded PDF and multi-sheet Excel exports are a concrete, demoable enterprise feature that's cheap to build with the right libraries.

## Architecture
```
GET /reports/bookings?format=pdf|xlsx&from=...&to=...
        │
        ▼
ReportService.generate(type, format, filters)
        │
   ┌────┴────┐
   ▼         ▼
PDF path   Excel path
(Puppeteer  (ExcelJS
 renders     writes
 HTML        workbook
 template)   directly)
        │
        ▼
Stream response — never buffer the whole file in memory for large reports
```
For reports that take more than a couple seconds, generate asynchronously via a background job and let the client poll/download when ready (see Step 5).

## Step 1 — HTML report template (source of truth for the PDF)
```js
// src/reports/templates/bookingReport.template.js
function bookingReportHTML({ tenant, dateRange, bookings }) {
  const rows = bookings.map(b => `
    <tr>
      <td>${b.id.slice(0, 8)}</td>
      <td>${b.resource.name}</td>
      <td>${b.user.name}</td>
      <td><span class="badge badge-${b.status.toLowerCase()}">${b.status}</span></td>
      <td>${new Date(b.slot_start).toLocaleString()}</td>
    </tr>
  `).join('');

  return `
  <!DOCTYPE html>
  <html>
  <head>
    <meta charset="utf-8" />
    <style>
      body { font-family: 'Helvetica Neue', Arial, sans-serif; color: #0f172a; margin: 40px; }
      .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #2563eb; padding-bottom: 16px; margin-bottom: 24px; }
      .header img { height: 40px; }
      h1 { font-size: 20px; margin: 0; }
      table { width: 100%; border-collapse: collapse; font-size: 12px; }
      th { text-align: left; background: #f1f5f9; padding: 8px; border-bottom: 1px solid #e2e8f0; }
      td { padding: 8px; border-bottom: 1px solid #f1f5f9; }
      .badge { padding: 2px 8px; border-radius: 999px; font-size: 10px; font-weight: 600; }
      .badge-confirmed { background: #dbeafe; color: #1e40af; }
      .badge-completed { background: #dcfce7; color: #166534; }
      .badge-cancelled { background: #f1f5f9; color: #475569; }
      .footer { margin-top: 24px; font-size: 10px; color: #94a3b8; }
    </style>
  </head>
  <body>
    <div class="header">
      <h1>${tenant.name} — Booking Report</h1>
      <span>${dateRange.from} to ${dateRange.to}</span>
    </div>
    <table>
      <thead><tr><th>ID</th><th>Resource</th><th>User</th><th>Status</th><th>Slot</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="footer">Generated ${new Date().toISOString()} — ${bookings.length} records</div>
  </body>
  </html>`;
}

module.exports = { bookingReportHTML };
```

## Step 2 — PDF generation with Puppeteer
```js
// src/reports/pdf.service.js
const puppeteer = require('puppeteer');

async function renderPDF(html) {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'], // required in most container/CI environments
  });
  try {
    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'networkidle0' });
    const buffer = await page.pdf({
      format: 'A4',
      printBackground: true,
      margin: { top: '20px', bottom: '20px', left: '20px', right: '20px' },
    });
    return buffer;
  } finally {
    await browser.close(); // always close, even on error, or the process leaks
  }
}

module.exports = { renderPDF };
```

## Step 3 — Multi-sheet Excel export with ExcelJS
```js
// src/reports/excel.service.js
const ExcelJS = require('exceljs');

async function generateBookingWorkbook({ bookings, resources, tenant }) {
  const workbook = new ExcelJS.Workbook();
  workbook.creator = tenant.name;
  workbook.created = new Date();

  const summarySheet = workbook.addWorksheet('Summary');
  summarySheet.columns = [
    { header: 'Metric', key: 'metric', width: 30 },
    { header: 'Value', key: 'value', width: 20 },
  ];
  summarySheet.addRows([
    { metric: 'Total Bookings', value: bookings.length },
    { metric: 'Confirmed', value: bookings.filter(b => b.status === 'CONFIRMED').length },
    { metric: 'Cancelled', value: bookings.filter(b => b.status === 'CANCELLED').length },
  ]);
  summarySheet.getRow(1).font = { bold: true };

  const bookingsSheet = workbook.addWorksheet('Bookings');
  bookingsSheet.columns = [
    { header: 'ID', key: 'id', width: 12 },
    { header: 'Resource', key: 'resource', width: 24 },
    { header: 'User', key: 'user', width: 24 },
    { header: 'Status', key: 'status', width: 14 },
    { header: 'Start', key: 'start', width: 20 },
    { header: 'End', key: 'end', width: 20 },
  ];
  bookingsSheet.getRow(1).font = { bold: true };
  bookingsSheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE2E8F0' } };

  bookings.forEach(b => {
    const row = bookingsSheet.addRow({
      id: b.id.slice(0, 8), resource: b.resource.name, user: b.user.name,
      status: b.status, start: b.slot_start, end: b.slot_end,
    });
    if (b.status === 'CANCELLED') row.font = { color: { argb: 'FF94A3B8' } };
  });

  const resourceSheet = workbook.addWorksheet('Resources');
  resourceSheet.columns = [
    { header: 'Name', key: 'name', width: 24 },
    { header: 'Utilization %', key: 'util', width: 16 },
  ];
  resources.forEach(r => resourceSheet.addRow({ name: r.name, util: r.utilizationPercent }));

  return workbook;
}

module.exports = { generateBookingWorkbook };
```

## Step 4 — Express endpoints with streaming download
```js
// src/routes/report.routes.js
const router = require('express').Router();
const { renderPDF } = require('../reports/pdf.service');
const { generateBookingWorkbook } = require('../reports/excel.service');
const { bookingReportHTML } = require('../reports/templates/bookingReport.template');
const reportDataService = require('../services/reportData.service');

router.get('/reports/bookings', authenticate, authorize('ADMIN', 'TENANT_ADMIN'), async (req, res, next) => {
  try {
    const { format = 'pdf', from, to } = req.query;
    const data = await reportDataService.getBookingReportData(req.user.tenantId, { from, to });

    if (format === 'pdf') {
      const html = bookingReportHTML(data);
      const buffer = await renderPDF(html);
      res.setHeader('Content-Type', 'application/pdf');
      res.setHeader('Content-Disposition', `attachment; filename="booking-report-${Date.now()}.pdf"`);
      return res.send(buffer);
    }

    if (format === 'xlsx') {
      const workbook = await generateBookingWorkbook(data);
      res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      res.setHeader('Content-Disposition', `attachment; filename="booking-report-${Date.now()}.xlsx"`);
      await workbook.xlsx.write(res); // streams directly to the response, no buffering the whole file
      return res.end();
    }

    return res.status(400).json({ error: 'format must be pdf or xlsx', code: 'VALIDATION_ERROR' });
  } catch (err) { next(err); }
});

module.exports = router;
```

## Step 5 — Async generation for large reports (background job pattern)
```js
// src/reports/reportJob.service.js — use when a report could take >3-4 seconds
const { v4: uuid } = require('uuid');
const jobs = new Map(); // swap for Redis/DB table in a longer-running project

async function queueReport(type, format, params) {
  const jobId = uuid();
  jobs.set(jobId, { status: 'PENDING' });

  // fire and forget; in production this would be a real queue (BullMQ, etc.)
  generateReportAsync(jobId, type, format, params);
  return jobId;
}

async function generateReportAsync(jobId, type, format, params) {
  try {
    jobs.set(jobId, { status: 'PROCESSING' });
    const data = await reportDataService.getBookingReportData(params.tenantId, params);
    const buffer = format === 'pdf'
      ? await renderPDF(bookingReportHTML(data))
      : (await generateBookingWorkbook(data)).xlsx.writeBuffer();
    jobs.set(jobId, { status: 'DONE', buffer: await buffer });
  } catch (err) {
    jobs.set(jobId, { status: 'FAILED', error: err.message });
  }
}

function getJobStatus(jobId) { return jobs.get(jobId); }

module.exports = { queueReport, getJobStatus };
```
```js
// routes
router.post('/reports/bookings/async', authenticate, async (req, res) => {
  const jobId = await queueReport('bookings', req.body.format, { tenantId: req.user.tenantId, ...req.body });
  res.status(202).json({ jobId, statusUrl: `/reports/jobs/${jobId}` });
});

router.get('/reports/jobs/:jobId', authenticate, (req, res) => {
  const job = getJobStatus(req.params.jobId);
  if (!job) return res.status(404).json({ error: 'Job not found' });
  if (job.status !== 'DONE') return res.json({ status: job.status });
  res.setHeader('Content-Type', 'application/octet-stream');
  res.send(job.buffer);
});
```

## Enterprise best practices
- Reuse the launched Puppeteer browser instance across requests instead of launching per-request if report volume is high — launching is the slowest part (~1-2s). For hackathon scope, per-request launch is fine and simpler to reason about.
- Never generate reports synchronously inside a request handler for anything that scans a large table — always paginate the underlying query or move to the async job pattern above.
- Brand every report (logo, tenant name, generated timestamp) — a plain data dump reads as unfinished.

## Checklist before moving on
- [ ] PDF and Excel both tested with zero rows (empty state, doesn't crash)
- [ ] Puppeteer browser always closed in a `finally` block
- [ ] Excel export streams (`workbook.xlsx.write(res)`), not buffered fully for large datasets
- [ ] Report routes are role-gated (`ADMIN`/`TENANT_ADMIN` only) — reports often expose cross-user data
- [ ] Async job endpoint returns `202` with a poll URL, not a blocking response

## Common mistakes to avoid
- Forgetting `printBackground: true` in Puppeteer's PDF options — badges/colors silently disappear.
- Not setting `Content-Disposition` — browsers try to render the PDF/Excel inline instead of downloading.
- Blocking the event loop with a large synchronous report generation — always confirm response time before demo day with your real (or realistically-sized seeded) dataset.

## Integration with the rest of the stack
- Report data queries should respect **Multi-Tenant Data Isolation** — always scope by `tenantId`.
- Report generation events can optionally be recorded in the **Immutable Audit Log** (`action: 'REPORT_GENERATED'`) for a compliance-style trail.

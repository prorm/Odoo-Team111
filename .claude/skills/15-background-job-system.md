# Background Job System

## Why this matters
Every real ERP defers slow work — report generation, notification fan-out, AI decision sweeps, cleanup jobs — off the request/response cycle. A judge watching `POST /reports` block for 45 seconds reads as amateur; the same feature returning instantly with a job ID and a status you can poll reads as infrastructure maturity. This is the single highest-leverage "looks enterprise" addition you can make, and it's the backbone every other async skill in this library (Reporting, Notifications, AI Decisions) should sit on top of.

## Architecture
```
API request
    │
    ▼
enqueue(queueName, jobData)  — returns immediately with a jobId
    │
    ▼
Redis (BullMQ's backing store)
    │
    ▼
Worker process(es) — separate from the API process, pull jobs and execute
    │
    ├── on success → job marked completed, result stored
    ├── on failure → retried with backoff, then moved to a dead-letter state
    └── progress updates → pushed via WebSocket to the client watching the job
    │
    ▼
Client polls GET /jobs/:id OR listens for job:completed over the socket
```
Workers run as a **separate Node process** from your API server — this is the actual architectural distinction that makes it "background," not just an `async` function.

## Step 1 — Install and configure BullMQ
```bash
npm install bullmq ioredis
```
```js
// src/queues/connection.js
const { Redis } = require('ioredis');

const connection = new Redis(process.env.REDIS_URL || 'redis://localhost:6379', {
  maxRetriesPerRequest: null, // required by BullMQ
});

module.exports = connection;
```

## Step 2 — Define queues
```js
// src/queues/index.js
const { Queue } = require('bullmq');
const connection = require('./connection');

const reportQueue = new Queue('reports', { connection });
const notificationQueue = new Queue('notifications', { connection });
const aiSweepQueue = new Queue('ai-sweep', { connection });

const DEFAULT_JOB_OPTIONS = {
  attempts: 3,
  backoff: { type: 'exponential', delay: 2000 }, // 2s, 4s, 8s
  removeOnComplete: { count: 100 },  // keep last 100 for inspection, don't grow Redis forever
  removeOnFail: { count: 500 },
};

module.exports = { reportQueue, notificationQueue, aiSweepQueue, DEFAULT_JOB_OPTIONS };
```

## Step 3 — Enqueue from the API (returns instantly)
```js
// src/controllers/report.controller.js
const { reportQueue, DEFAULT_JOB_OPTIONS } = require('../queues');

async function requestReport(req, res, next) {
  try {
    const job = await reportQueue.add('generate-booking-report', {
      tenantId: req.user.tenantId,
      requestedBy: req.user.id,
      format: req.body.format,
      dateRange: req.body.dateRange,
    }, DEFAULT_JOB_OPTIONS);

    res.status(202).json({ jobId: job.id, statusUrl: `/jobs/${job.id}` });
  } catch (err) { next(err); }
}

module.exports = { requestReport };
```

## Step 4 — Worker process (run separately: `node src/workers/report.worker.js`)
```js
// src/workers/report.worker.js
const { Worker } = require('bullmq');
const connection = require('../queues/connection');
const { renderPDF } = require('../reports/pdf.service');
const { bookingReportHTML } = require('../reports/templates/bookingReport.template');
const reportDataService = require('../services/reportData.service');
const { getIO } = require('../realtime/socket');
const { logAudit } = require('../services/audit.service');
const prisma = require('../lib/prisma');

const worker = new Worker('reports', async (job) => {
  const { tenantId, requestedBy, format, dateRange } = job.data;

  await job.updateProgress(10);
  const data = await reportDataService.getBookingReportData(tenantId, dateRange);

  await job.updateProgress(50);
  const buffer = format === 'pdf'
    ? await renderPDF(bookingReportHTML(data))
    : await (await require('../reports/excel.service').generateBookingWorkbook(data)).xlsx.writeBuffer();

  await job.updateProgress(90);
  const url = await persistReportFile(job.id, buffer, format); // save to disk/S3, return a download URL

  await logAudit(prisma, { entity: 'Report', entityId: job.id, action: 'GENERATED', actorId: requestedBy, tenantId });

  return { url, format, recordCount: data.bookings.length };
}, { connection, concurrency: 4 }); // process up to 4 report jobs in parallel

worker.on('completed', (job, result) => {
  const io = getIO();
  io.to(`user:${job.data.requestedBy}`).emit('job:completed', { jobId: job.id, result });
});

worker.on('failed', (job, err) => {
  console.error(`[worker:reports] job ${job.id} failed after ${job.attemptsMade} attempts:`, err.message);
  const io = getIO();
  io.to(`user:${job.data.requestedBy}`).emit('job:failed', { jobId: job.id, error: err.message });
});

module.exports = worker;
```

## Step 5 — Job status polling endpoint
```js
// src/routes/job.routes.js
const router = require('express').Router();
const { Queue } = require('bullmq');
const connection = require('../queues/connection');

const queues = { reports: new Queue('reports', { connection }), notifications: new Queue('notifications', { connection }) };

router.get('/jobs/:queueName/:jobId', authenticate, async (req, res, next) => {
  try {
    const queue = queues[req.params.queueName];
    if (!queue) return res.status(404).json({ error: 'Unknown queue' });

    const job = await queue.getJob(req.params.jobId);
    if (!job) return res.status(404).json({ error: 'Job not found' });

    const state = await job.getState(); // 'waiting' | 'active' | 'completed' | 'failed' | 'delayed'
    res.json({
      id: job.id, state, progress: job.progress,
      result: state === 'completed' ? job.returnvalue : null,
      failedReason: state === 'failed' ? job.failedReason : null,
    });
  } catch (err) { next(err); }
});

module.exports = router;
```

## Step 6 — Scheduled/recurring jobs (replaces raw `setInterval` from earlier skills)
```js
// src/queues/schedulers.js
const { aiSweepQueue } = require('./index');

async function setupSchedulers() {
  await aiSweepQueue.add('sweep-stale-bookings', {}, {
    repeat: { every: 5 * 60 * 1000 }, // every 5 minutes, BullMQ manages this reliably across restarts
    jobId: 'sweep-stale-bookings-recurring', // fixed ID prevents duplicate schedulers on redeploy
  });
}

module.exports = { setupSchedulers };
```
This directly replaces the `setInterval` used in the AI Decision-Node and Idempotency cleanup skills — BullMQ's repeatable jobs survive process restarts and don't duplicate if you redeploy, which `setInterval` does not guarantee.

## Step 7 — Frontend: job status hook
```jsx
// src/hooks/useJobStatus.js
import { useState, useEffect } from 'react';
import { useSocketEvent } from '../realtime/SocketProvider';
import { api } from '../lib/api';

export function useJobStatus(queueName, jobId) {
  const [status, setStatus] = useState({ state: 'waiting', progress: 0, result: null });

  useEffect(() => {
    if (!jobId) return;
    const poll = setInterval(async () => {
      const { data } = await api.get(`/jobs/${queueName}/${jobId}`);
      setStatus(data);
      if (['completed', 'failed'].includes(data.state)) clearInterval(poll);
    }, 1500);
    return () => clearInterval(poll);
  }, [jobId]);

  // websocket push short-circuits the poll the instant it's actually done
  useSocketEvent('job:completed', (payload) => {
    if (payload.jobId === jobId) setStatus(s => ({ ...s, state: 'completed', result: payload.result }));
  });

  return status;
}
```
```jsx
function ReportButton() {
  const [jobId, setJobId] = useState(null);
  const status = useJobStatus('reports', jobId);

  const requestReport = async () => {
    const { data } = await api.post('/reports/bookings', { format: 'pdf', dateRange: {...} });
    setJobId(data.jobId);
  };

  return (
    <div>
      <Button onClick={requestReport} loading={status.state === 'active'}>Generate Report</Button>
      {status.state === 'active' && <ProgressBar value={status.progress} />}
      {status.state === 'completed' && <a href={status.result.url}>Download Report</a>}
    </div>
  );
}
```

## Enterprise best practices
- Workers are a **separate process** from the API server (`npm run worker` alongside `npm run start`) — this is the real distinction, not just wrapping code in a `setTimeout`.
- Set `attempts` + exponential `backoff` on every job — transient failures (a flaky external API call) shouldn't need manual intervention.
- Bound queue growth with `removeOnComplete`/`removeOnFail` counts — an unbounded Redis queue is a memory leak waiting to happen.
- Concurrency tuned per worker (`concurrency: 4`) based on what the job actually does — CPU-heavy PDF rendering wants lower concurrency than a lightweight notification send.

## Checklist before moving on
- [ ] Worker runs as its own process, started independently of the API server
- [ ] Every job has `attempts` + `backoff` configured, not left to fail silently on first error
- [ ] Job status is pollable via REST AND pushed via WebSocket (redundant paths, demo-safe)
- [ ] Recurring jobs use BullMQ's `repeat` option with a fixed `jobId`, not a raw `setInterval`
- [ ] Failed jobs are logged with enough context to debug without re-running

## Common mistakes to avoid
- Running the worker in the same process as the API and calling it "background" — under real concurrent load this still blocks the event loop for CPU-bound work (like Puppeteer PDF rendering).
- No retry/backoff — a single transient failure (Redis blip, external API timeout) permanently fails the job.
- Forgetting `maxRetriesPerRequest: null` on the Redis connection — BullMQ requires it and will throw confusingly without it.

## Integration with the rest of the stack
- The **Data Export & Reporting Engine** skill's async path should be rebuilt on this queue instead of the `Map`-based job tracker shown there — this is the production version of that pattern.
- The **AI Decision-Node**'s sweep job and the **Idempotent API Design**'s cleanup job both move here as BullMQ repeatable jobs.
- Job completion emits over the same **WebSocket Real-Time Sync** socket infrastructure, reusing `getIO()` and the `user:<id>` room convention.

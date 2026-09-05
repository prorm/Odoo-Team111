# Metrics & Health Monitoring

## Why this matters
This is the single most visually impressive addition on the list for the least code: a live dashboard showing requests/sec, latency, queue length, and connected users looks like Datadog, and judges immediately recognize it as "these people have run something in production before." Paired with `/health` endpoints, it also answers the unglamorous-but-real question every enterprise system must answer: "how do we know if this is actually working right now?"

## Architecture
```
Every request/socket event/job
        │
        ▼
MetricsCollector — in-memory counters + histograms (prom-client)
        │
        ├── /metrics — Prometheus-format scrape endpoint (the enterprise-standard interface)
        ├── /health, /health/database, /health/redis, /health/socket, /health/storage
        └── /admin/metrics/dashboard — a live-updating custom UI for judges to actually see
        │
        ▼
WebSocket push of a metrics snapshot every 2s → React dashboard renders live charts
```
`/metrics` in Prometheus format is what makes this "real" rather than a demo toy — any actual observability stack (Grafana, Datadog) can scrape it unmodified. The custom live dashboard is what you actually put on screen for judges, because a raw Prometheus text page isn't visually compelling.

## Step 1 — Install and set up prom-client
```bash
npm install prom-client
```
```js
// src/metrics/registry.js
const client = require('prom-client');

const register = new client.Registry();
client.collectDefaultMetrics({ register }); // process CPU, memory, event loop lag — free, built in

const httpRequestDuration = new client.Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP request duration in seconds',
  labelNames: ['method', 'route', 'status_code'],
  buckets: [0.01, 0.05, 0.1, 0.3, 0.5, 1, 2, 5],
  registers: [register],
});

const httpRequestsTotal = new client.Counter({
  name: 'http_requests_total',
  help: 'Total HTTP requests',
  labelNames: ['method', 'route', 'status_code'],
  registers: [register],
});

const bookingsCreatedTotal = new client.Counter({
  name: 'bookings_created_total',
  help: 'Total bookings created',
  registers: [register],
});

const bookingConflictsTotal = new client.Counter({
  name: 'booking_conflicts_total',
  help: 'Total booking conflicts rejected by the exclusion constraint',
  registers: [register],
});

const websocketConnectionsGauge = new client.Gauge({
  name: 'websocket_connections_current',
  help: 'Currently connected WebSocket clients',
  registers: [register],
});

const queueLengthGauge = new client.Gauge({
  name: 'queue_length',
  help: 'Current job queue length',
  labelNames: ['queue'],
  registers: [register],
});

const cacheHitRatio = new client.Gauge({
  name: 'cache_hit_ratio',
  help: 'Rolling cache hit ratio (0-1)',
  registers: [register],
});

module.exports = {
  register, httpRequestDuration, httpRequestsTotal, bookingsCreatedTotal,
  bookingConflictsTotal, websocketConnectionsGauge, queueLengthGauge, cacheHitRatio,
};
```

## Step 2 — HTTP instrumentation middleware
```js
// src/middleware/metricsMiddleware.js
const { httpRequestDuration, httpRequestsTotal } = require('../metrics/registry');

function metricsMiddleware(req, res, next) {
  const start = process.hrtime.bigint();
  res.on('finish', () => {
    const durationSec = Number(process.hrtime.bigint() - start) / 1e9;
    const route = req.route?.path || req.path; // fall back to raw path if no matched route
    const labels = { method: req.method, route, status_code: res.statusCode };
    httpRequestDuration.observe(labels, durationSec);
    httpRequestsTotal.inc(labels);
  });
  next();
}

module.exports = metricsMiddleware;
```
```js
// app.js — register early, before routes, so every request is timed
app.use(metricsMiddleware);
```

## Step 3 — Domain-specific metrics wired into existing services
```js
// src/services/booking.service.js — extends the State Machine skill
const { bookingsCreatedTotal, bookingConflictsTotal } = require('../metrics/registry');

async function createBooking(data) {
  try {
    const booking = await /* ...existing creation logic... */;
    bookingsCreatedTotal.inc();
    return booking;
  } catch (err) {
    if (err.code === 'SLOT_CONFLICT') bookingConflictsTotal.inc();
    throw err;
  }
}
```
```js
// src/realtime/socket.js — extends the WebSocket skill
const { websocketConnectionsGauge } = require('../metrics/registry');

io.on('connection', (socket) => {
  websocketConnectionsGauge.inc();
  socket.on('disconnect', () => websocketConnectionsGauge.dec());
});
```
```js
// src/queues/index.js — extends the Background Job System skill, polled periodically
const { queueLengthGauge } = require('../metrics/registry');

async function updateQueueMetrics() {
  const waiting = await reportQueue.getWaitingCount();
  queueLengthGauge.set({ queue: 'reports' }, waiting);
}
setInterval(updateQueueMetrics, 5000);
```

## Step 4 — Prometheus scrape endpoint
```js
// src/routes/metrics.routes.js
const router = require('express').Router();
const { register } = require('../metrics/registry');

router.get('/metrics', authenticate, authorize('ADMIN'), async (req, res) => {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
});

module.exports = router;
```
Gate this behind admin auth — a Prometheus endpoint exposed publicly leaks internal operational detail (request patterns, error rates) that shouldn't be open to arbitrary users.

## Step 5 — Health check endpoints
```js
// src/routes/health.routes.js
const router = require('express').Router();
const prisma = require('../lib/prisma');
const redis = require('../lib/redis');
const { getIO } = require('../realtime/socket');

router.get('/health', (req, res) => res.json({ status: 'ok', uptime: process.uptime() }));

router.get('/health/database', async (req, res) => {
  try {
    await prisma.$queryRaw`SELECT 1`;
    res.json({ status: 'ok' });
  } catch (err) {
    res.status(503).json({ status: 'down', error: err.message });
  }
});

router.get('/health/redis', async (req, res) => {
  try {
    const pong = await redis.ping();
    res.json({ status: pong === 'PONG' ? 'ok' : 'degraded' });
  } catch (err) {
    res.status(503).json({ status: 'down', error: err.message });
  }
});

router.get('/health/socket', (req, res) => {
  try {
    const io = getIO();
    res.json({ status: 'ok', connections: io.engine.clientsCount });
  } catch (err) {
    res.status(503).json({ status: 'down', error: err.message });
  }
});

router.get('/health/storage', async (req, res) => {
  try {
    await require('fs/promises').access(process.env.UPLOAD_DIR || './uploads');
    res.json({ status: 'ok' });
  } catch (err) {
    res.status(503).json({ status: 'down', error: err.message });
  }
});

router.get('/health/full', async (req, res) => {
  const checks = await Promise.allSettled([
    prisma.$queryRaw`SELECT 1`.then(() => ({ name: 'database', status: 'ok' })),
    redis.ping().then(() => ({ name: 'redis', status: 'ok' })),
  ]);
  const results = checks.map((c, i) => c.status === 'fulfilled' ? c.value : { name: ['database', 'redis'][i], status: 'down' });
  const allOk = results.every(r => r.status === 'ok');
  res.status(allOk ? 200 : 503).json({ status: allOk ? 'ok' : 'degraded', checks: results });
});

module.exports = router;
```
`/health/full` is what a load balancer or uptime monitor would actually poll; the individual `/health/<dependency>` endpoints are what you show a judge who asks "how do you know if Redis is down?"

## Step 6 — Live metrics snapshot pushed over WebSocket
```js
// src/metrics/liveSnapshot.js
const { register } = require('./registry');
const { getIO } = require('../realtime/socket');

async function pushSnapshot() {
  const metrics = await register.getMetricsAsJSON();
  const snapshot = summarize(metrics); // pick the handful of numbers worth graphing live
  getIO().to('role:ADMIN').emit('metrics:snapshot', snapshot);
}

function summarize(metrics) {
  const find = (name) => metrics.find(m => m.name === name);
  return {
    timestamp: Date.now(),
    requestsTotal: sumCounter(find('http_requests_total')),
    avgLatencyMs: avgHistogram(find('http_request_duration_seconds')) * 1000,
    bookingsCreated: sumCounter(find('bookings_created_total')),
    bookingConflicts: sumCounter(find('booking_conflicts_total')),
    websocketConnections: find('websocket_connections_current')?.values?.[0]?.value ?? 0,
    queueLength: sumGauge(find('queue_length')),
  };
}
// helper reducers (sumCounter/avgHistogram/sumGauge) omitted for brevity — sum/average the .values array

setInterval(pushSnapshot, 2000);
module.exports = { pushSnapshot };
```

## Step 7 — Frontend: live metrics dashboard
```jsx
// src/pages/MetricsDashboard.jsx
import { useState } from 'react';
import { useSocketEvent } from '../realtime/SocketProvider';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

function MetricsDashboard() {
  const [history, setHistory] = useState([]);
  const [latest, setLatest] = useState(null);

  useSocketEvent('metrics:snapshot', (snapshot) => {
    setLatest(snapshot);
    setHistory(h => [...h.slice(-59), snapshot]); // keep a rolling 2-minute window at 2s intervals
  });

  return (
    <div className="grid grid-cols-4 gap-4">
      <StatCard label="Requests" value={latest?.requestsTotal ?? '—'} />
      <StatCard label="Avg Latency" value={latest ? `${latest.avgLatencyMs.toFixed(0)}ms` : '—'} />
      <StatCard label="Users Online" value={latest?.websocketConnections ?? '—'} />
      <StatCard label="Queue Length" value={latest?.queueLength ?? '—'} />

      <div className="col-span-4 h-64">
        <ResponsiveContainer>
          <LineChart data={history}>
            <XAxis dataKey="timestamp" tickFormatter={t => new Date(t).toLocaleTimeString()} />
            <YAxis />
            <Tooltip labelFormatter={t => new Date(t).toLocaleTimeString()} />
            <Line type="monotone" dataKey="avgLatencyMs" stroke="#2563eb" dot={false} name="Latency (ms)" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="col-span-4 h-64">
        <ResponsiveContainer>
          <LineChart data={history}>
            <XAxis dataKey="timestamp" tickFormatter={t => new Date(t).toLocaleTimeString()} />
            <YAxis />
            <Tooltip labelFormatter={t => new Date(t).toLocaleTimeString()} />
            <Line type="monotone" dataKey="bookingsCreated" stroke="#16a34a" dot={false} name="Bookings" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  );
}
```

## Demo tip
Open this dashboard in a third tab. While demoing the other two role dashboards, glance over and let the judges see request counts and latency ticking up live as actions happen — you don't need to narrate it, seeing it update in real time next to the actual product is the point. If you have the **Concurrent Constraint Demo Kit** script running, the `booking_conflicts_total` counter incrementing on screen is a strong visual pairing with the "break it live" moment.

## Enterprise best practices
- Use `prom-client`'s standard metric types correctly: `Counter` only increases (requests, conflicts), `Gauge` goes up and down (connections, queue length), `Histogram` for distributions you want percentiles from (latency).
- `collectDefaultMetrics()` gives you process-level metrics (memory, CPU, event loop lag) for free — always enable it, it's one line and genuinely useful for spotting a leak during a long demo day.
- Gate `/metrics` behind admin auth; keep `/health` endpoints public (or lightly protected) since uptime monitors and load balancers need to reach them without a session.

## Checklist before moving on
- [ ] `/health/full` returns `503` if any real dependency (DB, Redis) is down, not always `200`
- [ ] `/metrics` is Prometheus-format and admin-gated
- [ ] At least 4-5 domain-specific metrics wired in (not just default process metrics)
- [ ] Live dashboard updates via WebSocket push, not manual refresh
- [ ] Dashboard tested to survive a couple minutes running so the charts actually show a trend line, not one flat point

## Common mistakes to avoid
- Using a `Counter` for something that can decrease (like connection count) — use a `Gauge`; `Counter.dec()` doesn't exist and you'll hit a runtime error.
- Exposing `/metrics` with no auth — it's a data leak (request volume, error rates, internal route names) most real deployments protect.
- Building the dashboard to poll via REST every second instead of pushing over the socket — unnecessary load and it fights with the "real-time" story the rest of the app is telling.

## Integration with the rest of the stack
- Reuses the exact WebSocket infrastructure and `role:ADMIN` room convention from **WebSocket Real-Time Sync**.
- Queue length metrics read directly from the **Background Job System**'s BullMQ queues.
- `booking_conflicts_total` is the metrics-layer counterpart to the **Concurrent Constraint Demo Kit**'s live proof — the same event, now also visible as a running counter.

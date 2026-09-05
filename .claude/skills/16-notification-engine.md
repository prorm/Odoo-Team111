# Notification Engine

## Why this matters
Real ERP systems don't just show data in-app — they push it to wherever the user actually is: email, in-app bell icon, live socket toast, sometimes SMS or Slack. A single unified notification service (one call site, many delivery channels) is a concrete enterprise pattern that's cheap to build and highly demoable — trigger one event, show it landing in three places at once.

## Architecture
```
Any service call: notify(userId, { type, title, body, data })
        │
        ▼
NotificationService.send()
        │
        ├── Persist to DB (in-app notification, always — source of truth)
        ├── Emit over WebSocket (if user is connected) — instant in-app toast
        ├── Enqueue email job (Background Job System) — reliable, doesn't block the request
        ├── Enqueue Slack webhook job (if tenant has Slack integration configured)
        └── SMS (optional, only for high-priority types — costs money per message)
        │
        ▼
User preferences table gates which channels are actually used per user/type
```
The in-app DB record is the source of truth — every other channel is a delivery attempt on top of it. If email fails, the user still sees it in their notification bell.

## Step 1 — Schema
```prisma
model Notification {
  id        String   @id @default(uuid())
  userId    String
  tenantId  String?
  type      String   // 'booking_confirmed', 'booking_cancelled', 'report_ready', etc.
  title     String
  body      String
  data      Json?    // structured payload for deep-linking, e.g. { bookingId: '...' }
  readAt    DateTime?
  createdAt DateTime @default(now())

  user      User     @relation(fields: [userId], references: [id])

  @@index([userId, readAt, createdAt])
}

model NotificationPreference {
  id       String  @id @default(uuid())
  userId   String
  type     String  // matches Notification.type
  channel  String  // 'email' | 'sms' | 'slack' | 'inApp'
  enabled  Boolean @default(true)

  @@unique([userId, type, channel])
}
```

## Step 2 — Unified service, single call site
```js
// src/services/notification.service.js
const prisma = require('../lib/prisma');
const { getIO } = require('../realtime/socket');
const { notificationQueue, DEFAULT_JOB_OPTIONS } = require('../queues');

const CHANNEL_DEFAULTS = { email: true, inApp: true, sms: false, slack: false };

async function notify(userId, { type, title, body, data = null, tenantId = null }) {
  const notification = await prisma.notification.create({
    data: { userId, tenantId, type, title, body, data },
  });

  const io = getIO();
  io.to(`user:${userId}`).emit('notification:new', notification); // instant in-app toast if connected

  const prefs = await resolveChannels(userId, type);

  if (prefs.email) {
    await notificationQueue.add('send-email', { userId, type, title, body, data }, DEFAULT_JOB_OPTIONS);
  }
  if (prefs.slack) {
    await notificationQueue.add('send-slack', { userId, tenantId, title, body }, DEFAULT_JOB_OPTIONS);
  }
  if (prefs.sms && isHighPriority(type)) {
    await notificationQueue.add('send-sms', { userId, body }, DEFAULT_JOB_OPTIONS);
  }

  return notification;
}

async function resolveChannels(userId, type) {
  const rows = await prisma.notificationPreference.findMany({ where: { userId, type } });
  const resolved = { ...CHANNEL_DEFAULTS };
  rows.forEach(r => { resolved[r.channel] = r.enabled; });
  return resolved;
}

function isHighPriority(type) {
  return ['booking_cancelled', 'payment_failed', 'account_locked'].includes(type);
}

module.exports = { notify };
```

## Step 3 — Worker: per-channel delivery (runs on the Background Job System)
```js
// src/workers/notification.worker.js
const { Worker } = require('bullmq');
const connection = require('../queues/connection');
const { sendEmail } = require('../channels/email.channel');
const { sendSlack } = require('../channels/slack.channel');
const { sendSMS } = require('../channels/sms.channel');

const handlers = {
  'send-email': (data) => sendEmail(data),
  'send-slack': (data) => sendSlack(data),
  'send-sms': (data) => sendSMS(data),
};

const worker = new Worker('notifications', async (job) => {
  const handler = handlers[job.name];
  if (!handler) throw new Error(`No handler for notification job type: ${job.name}`);
  return handler(job.data);
}, { connection, concurrency: 10 });

worker.on('failed', (job, err) => {
  console.error(`[worker:notifications] ${job.name} failed for user ${job.data.userId}:`, err.message);
});

module.exports = worker;
```

## Step 4 — Channel implementations
```js
// src/channels/email.channel.js
const nodemailer = require('nodemailer');
const prisma = require('../lib/prisma');

const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST, port: process.env.SMTP_PORT,
  auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
});

async function sendEmail({ userId, title, body }) {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user?.email) return;
  await transporter.sendMail({
    from: '"YourApp" <notifications@yourapp.com>',
    to: user.email,
    subject: title,
    html: `<div style="font-family: sans-serif; padding: 24px;"><h2>${title}</h2><p>${body}</p></div>`,
  });
}

module.exports = { sendEmail };
```
```js
// src/channels/slack.channel.js
const axios = require('axios');
const prisma = require('../lib/prisma');

async function sendSlack({ tenantId, title, body }) {
  const tenant = await prisma.tenant.findUnique({ where: { id: tenantId } });
  if (!tenant?.slackWebhookUrl) return; // tenant hasn't configured Slack, skip silently
  await axios.post(tenant.slackWebhookUrl, {
    text: `*${title}*\n${body}`,
  });
}

module.exports = { sendSlack };
```
```js
// src/channels/sms.channel.js — Twilio example, stub-safe if no API key configured
const twilio = require('twilio');
const client = process.env.TWILIO_SID ? twilio(process.env.TWILIO_SID, process.env.TWILIO_AUTH_TOKEN) : null;
const prisma = require('../lib/prisma');

async function sendSMS({ userId, body }) {
  if (!client) return console.warn('[sms] Twilio not configured, skipping SMS delivery');
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user?.phone) return;
  await client.messages.create({ body, from: process.env.TWILIO_FROM, to: user.phone });
}

module.exports = { sendSMS };
```

## Step 5 — Triggering from existing domain events
```js
// extends the State Machine skill's transitionBooking
async function transitionBooking(bookingId, toStatus, actorId) {
  const updated = await /* ...existing transition logic... */;

  if (toStatus === 'CONFIRMED') {
    await notify(updated.userId, {
      type: 'booking_confirmed', title: 'Booking Confirmed',
      body: `Your booking for ${updated.resource.name} is confirmed.`,
      data: { bookingId: updated.id }, tenantId: updated.tenantId,
    });
  }
  if (toStatus === 'CANCELLED') {
    await notify(updated.userId, {
      type: 'booking_cancelled', title: 'Booking Cancelled',
      body: `Your booking for ${updated.resource.name} was cancelled.`,
      data: { bookingId: updated.id }, tenantId: updated.tenantId,
    });
  }
  return updated;
}
```

## Step 6 — API routes: inbox + preferences
```js
// src/routes/notification.routes.js
const router = require('express').Router();

router.get('/notifications', authenticate, async (req, res) => {
  const notifications = await prisma.notification.findMany({
    where: { userId: req.user.id },
    orderBy: { createdAt: 'desc' },
    take: 50,
  });
  res.json(notifications);
});

router.patch('/notifications/:id/read', authenticate, async (req, res) => {
  await prisma.notification.updateMany({
    where: { id: req.params.id, userId: req.user.id }, // scoped to the owner, never trust the ID alone
    data: { readAt: new Date() },
  });
  res.status(204).end();
});

router.put('/notifications/preferences', authenticate, async (req, res) => {
  const { type, channel, enabled } = req.body;
  await prisma.notificationPreference.upsert({
    where: { userId_type_channel: { userId: req.user.id, type, channel } },
    update: { enabled },
    create: { userId: req.user.id, type, channel, enabled },
  });
  res.status(204).end();
});

module.exports = router;
```

## Step 7 — Frontend: notification bell
```jsx
// src/components/NotificationBell.jsx
import { useState, useEffect } from 'react';
import { useSocketEvent } from '../realtime/SocketProvider';
import { useToast } from './common/Toast';
import { api } from '../lib/api';

function NotificationBell() {
  const [notifications, setNotifications] = useState([]);
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const unreadCount = notifications.filter(n => !n.readAt).length;

  useEffect(() => {
    api.get('/notifications').then(r => setNotifications(r.data));
  }, []);

  useSocketEvent('notification:new', (n) => {
    setNotifications(prev => [n, ...prev]);
    toast(n.title); // instant toast, in addition to landing in the bell
  });

  const markRead = async (id) => {
    await api.patch(`/notifications/${id}/read`);
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, readAt: new Date() } : n));
  };

  return (
    <div className="relative">
      <button onClick={() => setOpen(o => !o)} className="relative">
        <BellIcon />
        {unreadCount > 0 && <span className="absolute -top-1 -right-1 bg-red-500 text-white text-xs rounded-full w-4 h-4 flex items-center justify-center">{unreadCount}</span>}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-80 bg-white shadow-lg rounded-md border border-slate-200">
          {notifications.map(n => (
            <div key={n.id} onClick={() => markRead(n.id)}
              className={`p-3 border-b border-slate-100 cursor-pointer ${!n.readAt ? 'bg-blue-50' : ''}`}>
              <div className="font-medium text-sm">{n.title}</div>
              <div className="text-xs text-slate-500">{n.body}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

## Enterprise best practices
- Notifications are always persisted first, then fanned out — the in-app record is never lost even if every external channel fails.
- Per-user, per-type channel preferences (Step 1's `NotificationPreference`) — don't hardcode "everyone gets email for everything," real users unsubscribe from noisy types.
- High-priority-only gating for SMS (Step 2's `isHighPriority`) — SMS costs real money per message, don't fire it for every minor event.
- Channel delivery runs on the **Background Job System**, never inline in the request — an SMTP timeout should never slow down a booking confirmation response.

## Checklist before moving on
- [ ] `notify()` is the single call site used everywhere — no service reaches for `nodemailer` directly
- [ ] In-app notification persists even if all external channels fail
- [ ] Slack/SMS channels no-op gracefully (not crash) when not configured for a tenant/user
- [ ] Notification bell updates live via WebSocket, not just on page load
- [ ] Mark-as-read is scoped to `req.user.id`, preventing one user from marking another's notifications read

## Common mistakes to avoid
- Sending email inline in the request handler — a slow SMTP server directly slows down the user-facing API response.
- Firing every notification type to every channel regardless of preference — leads to alert fatigue and looks unpolished if a judge triggers 10 test actions and gets 10 emails.
- Not scoping notification reads/updates to the authenticated user — an IDOR (insecure direct object reference) bug judges may specifically probe for.

## Integration with the rest of the stack
- Delivery jobs run on the **Background Job System**'s `notificationQueue`.
- The live in-app push reuses the exact `getIO()` / `user:<id>` room convention from **WebSocket Real-Time Sync**.
- Notification events are a natural additional entry point into the **Immutable Audit Log** if you want a "who was notified about what, when" trail.

# Odoo Hackathon Playbooks (shared — works with any AI tool)

Paste the relevant section into Codex/Antigravity/Cursor as context before asking it to do that part of the work.

---

# Git Workflow Enforcer (Odoo Hackathon)

## Why this exists
Odoo disqualifies teams where the repo shows commits from only one person. Every one of the 4 teammates MUST have their own commits on their own branches. This skill exists to make that automatic and painless under time pressure.

## Branch naming
```
<initials>/<tag>-<short-description>
```
Examples: `rk/feat-booking-api`, `sm/fix-auth-guard`, `av/ui-dashboard-cards`

Tags: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`

## Commit message format (required)
```
[TAG] module: description
```
Examples:
- `[FEAT] booking: add exclusion constraint for time slot overlap`
- `[FIX] auth: correct role guard on admin routes`
- `[CHORE] setup: add websocket boilerplate`

Rules:
- TAG is always uppercase: FEAT, FIX, REFACTOR, CHORE, DOCS, TEST
- module = the folder/domain touched (auth, booking, dashboard, api, ui, db)
- description = imperative mood, lowercase, no period at the end
- One logical change per commit — don't bundle 5 features into one commit

## Setup checklist (do this in the first 30 minutes)
1. One person creates the repo and adds all 4 teammates as collaborators.
2. Each teammate clones and configures their own git identity:
   ```bash
   git config user.name "Your Real Name"
   git config user.email "your-actual-email@example.com"
   ```
   (Never let two people share one git identity — judges check `git log --format='%an %ae'`.)
3. Create a `main` branch (protected, only merged via PR) and a `dev` branch (integration branch).
4. Every teammate branches off `dev`, never commits directly to `main`.
5. Add a root `README.md` with team member names + GitHub usernames, mapped explicitly.

## Workflow during the hackathon
1. Pull latest `dev` before starting any new feature.
2. Branch: `git checkout -b <initials>/<tag>-<desc>`
3. Commit small and often (every 20-30 min of work, not one giant commit at the end).
4. Push your branch, open a PR into `dev`, self-merge is fine under time pressure but still open the PR (creates an auditable trail).
5. Rebase onto latest `dev` if there's a conflict — don't force-push over teammates' branches.

## Pre-submission audit (run this before final submission, non-negotiable)
```bash
git log --all --format='%an <%ae> — %s' | sort | uniq -c -w30
```
Confirm all 4 names appear with a meaningful number of commits each (not 1 commit each at the last minute — that looks staged). If one person is behind, have them commit something real (even docs, tests, or config) before the deadline.

## If asked to "just commit everything quickly"
Still push back gently: split the diff by whoever actually wrote each part if possible (`git add -p` to stage selectively), or at minimum make sure the commit author is set correctly for whoever is running the command. Never let convenience create a single-author repo.

---

# Role-Based Auth Scaffolder (Odoo Hackathon)

## When to use
Nearly every Odoo problem statement (QuickCourt, AssetFlow, civic tech, health platforms) requires at least 2-3 roles with strictly segregated dashboards. Build this ONCE, early, correctly — every other feature depends on it.

## Default stack assumption
Node/Express + JWT + MongoDB or Postgres, React frontend. If the team's actual stack differs, adapt the same structure (roles table/enum, middleware guard, protected routes) to that stack — the pattern matters more than the exact syntax.

## Step 1: Define roles explicitly
Ask/decide up front (don't guess mid-hackathon):
- What are the exact role names? (e.g. `admin`, `owner`, `user`)
- What can each role see/do that others can't?
- Can a user hold more than one role, or is it strictly one role per account?

Write this as a short table in the README before coding — this prevents "wait, can Owners also book?" confusion at hour 18.

## Step 2: Backend scaffold
```
/models/User.js        // role: enum['admin','owner','user'], required
/middleware/auth.js     // verifies JWT, attaches req.user
/middleware/rbac.js     // requireRole(['admin']) middleware factory
/routes/auth.js         // /register /login /me
```

`middleware/rbac.js` pattern:
```js
const requireRole = (...allowedRoles) => (req, res, next) => {
  if (!req.user) return res.status(401).json({ error: 'Not authenticated' });
  if (!allowedRoles.includes(req.user.role)) {
    return res.status(403).json({ error: 'Insufficient permissions' });
  }
  next();
};
module.exports = requireRole;
```

Usage on routes:
```js
router.get('/admin/reports', auth, requireRole('admin'), controller.getReports);
router.post('/bookings', auth, requireRole('owner', 'user'), controller.createBooking);
```

## Step 3: Frontend scaffold
```
/context/AuthContext.jsx   // holds user + role, exposes useAuth()
/routes/ProtectedRoute.jsx // <ProtectedRoute allowedRoles={['admin']}>
/pages/AdminDashboard.jsx
/pages/OwnerDashboard.jsx
/pages/UserDashboard.jsx
```

`ProtectedRoute.jsx` pattern:
```jsx
function ProtectedRoute({ allowedRoles, children }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" />;
  if (!allowedRoles.includes(user.role)) return <Navigate to="/unauthorized" />;
  return children;
}
```

Route each role to a DIFFERENT dashboard component on login — never one dashboard with conditional rendering everywhere (judges notice the difference, and it's more bug-prone).

## Step 4: Validation checklist before moving on
- [ ] Can't register as `admin` from the public signup form (admin accounts seeded/created separately)
- [ ] Every protected route has both `auth` AND `requireRole` middleware — auth alone isn't role protection
- [ ] Frontend role check is UX only — the REAL enforcement must be on the backend (never trust the client)
- [ ] JWT expiry set and refresh/logout flow works
- [ ] Passwords hashed (bcrypt), never stored plain or logged

## Common mistake to avoid
Don't let "admin can also do everything user can" become implicit and unenforced — either give admin explicit access to those routes too, or clearly say admin is management-only. Ambiguity here causes bugs discovered during the live demo.

---

# State Machine + Constraint Generator (Odoo Hackathon)

## Why this matters most
Per the judging criteria, the core of an ERP challenge is conflict avoidance — mathematically airtight constraints, not just "check in code before inserting." Judges will deliberately try to break this during Q&A or the demo. Build the DB-level guarantee, not just an application-level check.

## Step 1: Define the state machine explicitly
For every entity with a lifecycle, write out states and legal transitions BEFORE writing code:

```
Booking:  Pending → Confirmed → InProgress → Completed
                  ↘ Cancelled        ↘ Cancelled

Asset:    Available → Allocated → Maintenance → Available
                    ↘ Retired
```

Rules to enforce:
- No entity can skip states (Pending can't jump straight to Completed)
- No entity can move backward except through explicitly allowed transitions (e.g. Cancelled is terminal)
- Write this as a small `TRANSITIONS` map in code so it's enforced centrally, not scattered:

```js
const TRANSITIONS = {
  pending:     ['confirmed', 'cancelled'],
  confirmed:   ['in_progress', 'cancelled'],
  in_progress: ['completed'],
  completed:   [],
  cancelled:   [],
};

function canTransition(from, to) {
  return TRANSITIONS[from]?.includes(to) ?? false;
}
```
Call `canTransition` in a service layer before every state update — reject with a clean error if illegal.

## Step 2: Prevent overlap at the database level (not just app code)

**Postgres — exclusion constraint (preferred, this is what judges want to see):**
```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE bookings (
  id SERIAL PRIMARY KEY,
  resource_id INT NOT NULL,
  slot TSTZRANGE NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  EXCLUDE USING gist (
    resource_id WITH =,
    slot WITH &&
  ) WHERE (status != 'cancelled')
);
```
This makes it IMPOSSIBLE at the database level for two active bookings on the same resource to have overlapping time ranges — no race condition, no app-level bug can violate it. This is the strongest thing you can show a judge.

**If using MongoDB (no native exclusion constraints):**
Use a transaction + application-level check inside the transaction, with a unique compound index as a backstop:
```js
const session = await mongoose.startSession();
await session.withTransaction(async () => {
  const conflict = await Booking.findOne({
    resource_id, status: { $ne: 'cancelled' },
    $or: [{ start: { $lt: end }, end: { $gt: start } }]
  }).session(session);
  if (conflict) throw new Error('Slot already booked');
  await Booking.create([{ resource_id, start, end, status: 'pending' }], { session });
});
```
Be upfront with judges that Mongo can't do this at the schema level — mentioning this tradeoff explicitly signals you understand the architecture, which itself scores well.

**If using MySQL:** no native exclusion constraints either — use `SELECT ... FOR UPDATE` inside a transaction to lock the row range before checking overlap, same pattern as above.

## Step 3: Application-layer validation (defense in depth)
Even with DB constraints, validate at the service layer too so users get a clean error message instead of a raw DB error:
```js
try {
  await createBooking(data);
} catch (err) {
  if (err.code === '23P01') { // Postgres exclusion violation
    return res.status(409).json({ error: 'This slot is already booked. Please choose another time.' });
  }
  throw err;
}
```

## Step 4: Demo-readiness checklist
- [ ] Try to double-book the same resource/slot from two different sessions — confirm it's rejected with a clean message, not a 500 error
- [ ] Try an illegal state transition (e.g. Completed → Pending) — confirm it's rejected
- [ ] Have this ready as a live "watch me try to break it" moment in your demo (the prep doc explicitly recommends this)

---

# Input Validation & Sanitization Generator (Odoo Hackathon)

## Rule
Every single API endpoint that accepts a body, query param, or path param must validate before touching the database or business logic. No exceptions, even for "quick" endpoints added at hour 20.

## Backend pattern (Express + Zod — fast to write, good errors)
```js
const { z } = require('zod');

const createBookingSchema = z.object({
  resource_id: z.number().int().positive(),
  start: z.string().datetime(),
  end: z.string().datetime(),
  notes: z.string().max(500).optional(),
}).refine(d => new Date(d.end) > new Date(d.start), {
  message: 'end must be after start',
  path: ['end'],
});

function validate(schema) {
  return (req, res, next) => {
    const result = schema.safeParse(req.body);
    if (!result.success) {
      return res.status(400).json({
        error: 'Validation failed',
        details: result.error.flatten().fieldErrors,
      });
    }
    req.body = result.data; // use the parsed/sanitized version
    next();
  };
}

router.post('/bookings', auth, validate(createBookingSchema), controller.create);
```

If not using Zod (e.g. team prefers plain JS), at minimum hand-check for each field: type, required/optional, length bounds, format (email/date/enum), and reject unknown extra fields.

## What "absolute validation" means in practice — checklist per endpoint
- [ ] Type checked (string vs number vs boolean vs date)
- [ ] Required fields present, optional fields have safe defaults
- [ ] String length bounded (prevents huge payload abuse)
- [ ] Enum fields checked against an allow-list (e.g. `status` can only be one of the defined states — tie this to the state machine skill)
- [ ] IDs referencing other entities (e.g. `resource_id`) checked for existence before use, not just format
- [ ] No raw string concatenation into SQL — use parameterized queries / ORM methods only
- [ ] HTML/script content stripped or escaped if it will ever be rendered back (prevents stored XSS)
- [ ] File uploads (if any): type and size restricted server-side, not just via `accept=""` on the frontend

## Global error handler (catches anything that slips through)
```js
app.use((err, req, res, next) => {
  console.error(err); // keep server-side logs
  const status = err.statusCode || 500;
  const message = status === 500 ? 'Something went wrong' : err.message;
  res.status(status).json({ error: message });
});
```
Never leak stack traces or raw DB errors to the client response — that's an easy point loss in a "robust error handling" review, and a security smell.

## Frontend validation
Do it too (better UX), but treat it as convenience only — it is never a substitute for backend validation. If asked to "just validate on the frontend to save time," push back: at minimum keep the Zod schema shared between frontend and backend (same schema file, imported both places) so you get both without duplicating work.

---

# UI Component Kit (Odoo Hackathon)

## Why this exists
4 people building UI independently (across Claude, Codex, Antigravity) will produce visually inconsistent results unless everyone follows the same base rules. This skill is the shared contract — paste/reference it regardless of which AI tool you're using.

## Design tokens (agree on these as a team in the first hour, then never deviate)
```css
:root {
  --color-primary: #2563eb;
  --color-primary-hover: #1d4ed8;
  --color-success: #16a34a;
  --color-danger: #dc2626;
  --color-warning: #d97706;
  --color-bg: #f8fafc;
  --color-surface: #ffffff;
  --color-border: #e2e8f0;
  --color-text: #0f172a;
  --color-text-muted: #64748b;

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 16px;
  --space-4: 24px;
  --space-5: 32px;

  --shadow-card: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
}
```
Adjust hex values once as a team to match your product's branding, then EVERY component pulls from these variables — no hardcoded hex colors or px spacing anywhere.

## Every data-fetching component MUST have 3 states
This is the single most common thing missing under time pressure. Every component that fetches data needs:

```jsx
function ResourceList() {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  useEffect(() => {
    fetchResources()
      .then(data => setState({ status: 'success', data, error: null }))
      .catch(error => setState({ status: 'error', data: null, error: error.message }));
  }, []);

  if (state.status === 'loading') return <LoadingSkeleton />;
  if (state.status === 'error') return <ErrorState message={state.error} onRetry={() => window.location.reload()} />;
  if (!state.data?.length) return <EmptyState message="Nothing here yet" />;
  return <ResourceGrid items={state.data} />;
}
```

Shared components every teammate should reuse (build once, put in `/components/common/`):
- `<LoadingSkeleton />` — gray pulsing placeholder, not a spinner alone
- `<ErrorState message onRetry />` — icon + message + retry button
- `<EmptyState message />` — for zero-results states
- `<Button variant="primary|secondary|danger" loading disabled />`
- `<Card>` — base container using `--shadow-card`, `--radius-md`, `--space-3` padding
- `<Badge status="pending|confirmed|cancelled" />` — color-coded by state, tied to your state machine's states

## Layout rules
- Consistent page shell: sidebar/topnav + content area, same across all role dashboards (Admin/Owner/User) even though content differs
- Max content width ~1200px, centered, don't let cards stretch edge-to-edge on large screens
- Spacing between sections = `--space-4` or `--space-5`, spacing within a card = `--space-2` or `--space-3`
- Responsive breakpoint at minimum for mobile (stack columns, collapse sidebar to hamburger) — judges do check responsiveness

## Coordination tip for the team
Put this token file (`tokens.css` or `theme.js`) in the repo root as one of the FIRST commits, before anyone builds pages. Whoever's on Antigravity/Codex should be told explicitly "import and use these variables" rather than free-styling colors — that's the actual mechanism that keeps 4 people's UI consistent.

---

# WebSocket Real-Time Sync (Odoo Hackathon)

## When to use
Any time an action by one role should be visible to another role without them refreshing — e.g. Admin approves a request → Owner's dashboard updates instantly; User books a slot → Admin's live view shows it taken.

## Backend setup (Socket.IO — fastest to implement under time pressure)
```js
const { Server } = require('socket.io');
const io = new Server(httpServer, { cors: { origin: '*' } });

io.use((socket, next) => {
  // authenticate socket connection using the same JWT as REST
  const token = socket.handshake.auth.token;
  try {
    socket.user = verifyJwt(token);
    next();
  } catch {
    next(new Error('unauthorized'));
  }
});

io.on('connection', (socket) => {
  // join a room per role and/or per resource, so events only go to who needs them
  socket.join(`role:${socket.user.role}`);
  socket.join(`user:${socket.user.id}`);
});

module.exports = io;
```

Emit from wherever the state actually changes (service layer, right after DB write — not from the route handler, to guarantee it only fires on real success):
```js
// after successfully confirming a booking
io.to('role:admin').emit('booking:updated', { bookingId, status: 'confirmed' });
io.to(`user:${booking.userId}`).emit('booking:updated', { bookingId, status: 'confirmed' });
```

## Frontend setup
```jsx
// SocketContext.jsx
const socket = io(API_URL, { auth: { token: getToken() } });

useEffect(() => {
  socket.on('booking:updated', (payload) => {
    queryClient.invalidateQueries(['bookings']); // if using React Query
    // or: setBookings(prev => updateInPlace(prev, payload));
  });
  return () => socket.off('booking:updated');
}, []);
```

## Event naming convention (keep consistent across the team)
```
<entity>:<action>
booking:created
booking:updated
booking:cancelled
asset:allocated
asset:released
```
Agree on this list as a team before anyone starts wiring events — mismatched event names across frontend/backend teammates is the #1 time-waster with websockets under deadline pressure.

## Fallback if websockets break close to deadline
Don't let this block your demo. A simple polling fallback (`setInterval` refetch every 3-5s) is a safe backup — wire it behind a feature flag so you can flip to polling instantly if sockets misbehave live:
```js
const REALTIME_MODE = process.env.REALTIME_MODE || 'socket'; // 'socket' | 'poll'
```

## Demo tip
Show this with two browser windows side by side (different roles logged in) — action in one instantly reflects in the other with no refresh. This is a strong, low-effort "wow" moment for judges.

---

# Demo Script Builder (Odoo Hackathon)

## Input needed from the team
Before drafting, gather:
1. The finished feature list (what actually works, not what was planned)
2. The roles in the app and what each can do
3. The one constraint/state-machine safeguard you're proudest of (for the "break it" moment)
4. Any real-time / integration feature (websockets, Slack, GitHub) worth showing

## Structure (5-7 minutes, judges watch dozens of these — respect the runtime)

**0:00–0:30 — Problem framing**
One sentence on the real-world problem, one sentence on who it's for. Don't over-explain — judges read the problem statement already.

**0:30–1:30 — Architecture in one breath**
Stack, why you chose it, and the ONE architectural decision you're proud of (e.g. "we used a Postgres exclusion constraint so double-booking is impossible at the database level, not just checked in code"). This signals engineering maturity fast.

**1:30–4:30 — Live walkthrough across roles**
Show, don't tell. Sequence:
1. Log in as User → perform the core action (book/request/submit)
2. Switch to Admin/Owner view (ideally a second browser window already open) → show the action reflected live (real-time sync payoff)
3. Approve/reject something as Admin → show it reflect back to User instantly

**4:30–5:15 — The "break it" moment (required, do not skip)**
Deliberately attempt the illegal action on camera — try to double-book the same slot, or push an entity through an illegal state transition — and show the clean, friendly error message it returns instead of a crash. This is explicitly what judges want to see; don't bury it, name it out loud: "Now let's try to break it — I'll attempt to book this same slot twice."

**5:15–6:30 — Nice-to-haves, fast**
Rapid-fire whatever extra you built: offline caching, AI feature (explain WHY it's there, not just that it exists), Slack/GitHub integration, background jobs/audit reports. 10-15 seconds each, no lingering.

**6:30–7:00 — Close**
One sentence on what you'd build next with more time. Ends the demo on ambition, not apology.

## Rules for a strong demo
- Never present to a blank screen — have every screen already open in tabs before recording starts
- Script the exact words for the "break it" moment word-for-word — this is the highest-leverage 45 seconds, don't improvise it
- Cut anything that doesn't map to a judging criterion (must-have or nice-to-have) — a beautiful animation nobody asked about wastes time
- Record 2-3 takes; use the one where the live demo doesn't stutter

## Deliverable
Ask the team for their actual feature list, then produce a minute-by-minute script with the literal lines to say, tailored to their specific app — this skill is the template, the team fills in the specifics.

# Role-Based Auth Scaffolder

## Why this matters
Odoo judges score architectural rigor, not just "login works." Nearly every problem statement (QuickCourt, AssetFlow, civic/health platforms) requires 2-3 roles with strictly segregated dashboards and server-enforced permissions. A JWT that only gates the frontend is a fail here — enforcement must live in the database-adjacent service layer. This skill gives you the full stack: schema, middleware, permission helpers, and React guards, wired together so no route is ever accidentally exposed.

## Architecture
```
Client (React)
  └── AuthContext (holds user + role + permissions)
  └── ProtectedRoute (UX-level gate, redirects only)
        │
        ▼ Authorization: Bearer <JWT>
Express API
  └── authenticate middleware   (verifies JWT → req.user)
  └── authorize(...roles)       (role check → 403 if not allowed)
  └── can(permission)           (fine-grained permission check)
        │
        ▼
PostgreSQL (Prisma)
  └── User { role: Role enum }
  └── RolePermission (optional, for fine-grained ACL beyond simple roles)
```
Two layers of enforcement: **role** (coarse, e.g. `ADMIN`/`OWNER`/`USER`) and **permission** (fine, e.g. `booking:cancel:any` vs `booking:cancel:own`). Most hackathon problem statements only need roles; add permissions only if a role needs to act on "own" vs "any" records differently.

## Step 1 — Prisma schema
```prisma
// prisma/schema.prisma
enum Role {
  ADMIN
  OWNER
  USER
}

model User {
  id           String   @id @default(uuid())
  email        String   @unique
  passwordHash String
  name         String
  role         Role     @default(USER)
  createdAt    DateTime @default(now())
  refreshTokens RefreshToken[]

  @@index([role])
}

model RefreshToken {
  id        String   @id @default(uuid())
  token     String   @unique
  userId    String
  user      User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  expiresAt DateTime
  createdAt DateTime @default(now())
}
```
Run: `npx prisma migrate dev --name add_auth`

## Step 2 — Password hashing + token issuance
```js
// src/services/auth.service.js
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const prisma = require('../lib/prisma');

const ACCESS_TOKEN_TTL = '15m';
const REFRESH_TOKEN_TTL_DAYS = 7;

async function register({ email, password, name, role = 'USER' }) {
  const existing = await prisma.user.findUnique({ where: { email } });
  if (existing) {
    const err = new Error('Email already registered');
    err.statusCode = 409;
    throw err;
  }
  const passwordHash = await bcrypt.hash(password, 12);
  const user = await prisma.user.create({
    data: { email, passwordHash, name, role: role === 'ADMIN' ? 'USER' : role },
    // never allow public registration to self-elevate to ADMIN
  });
  return sanitize(user);
}

async function login({ email, password }) {
  const user = await prisma.user.findUnique({ where: { email } });
  if (!user || !(await bcrypt.compare(password, user.passwordHash))) {
    const err = new Error('Invalid credentials');
    err.statusCode = 401;
    throw err;
  }
  const accessToken = signAccessToken(user);
  const refreshToken = await issueRefreshToken(user.id);
  return { user: sanitize(user), accessToken, refreshToken };
}

function signAccessToken(user) {
  return jwt.sign(
    { sub: user.id, role: user.role, email: user.email },
    process.env.JWT_ACCESS_SECRET,
    { expiresIn: ACCESS_TOKEN_TTL }
  );
}

async function issueRefreshToken(userId) {
  const token = crypto.randomBytes(40).toString('hex');
  const expiresAt = new Date(Date.now() + REFRESH_TOKEN_TTL_DAYS * 24 * 60 * 60 * 1000);
  await prisma.refreshToken.create({ data: { token, userId, expiresAt } });
  return token;
}

async function rotateRefreshToken(oldToken) {
  const record = await prisma.refreshToken.findUnique({ where: { token: oldToken } });
  if (!record || record.expiresAt < new Date()) {
    const err = new Error('Refresh token invalid or expired');
    err.statusCode = 401;
    throw err;
  }
  await prisma.refreshToken.delete({ where: { id: record.id } }); // rotation: old token dies
  const user = await prisma.user.findUnique({ where: { id: record.userId } });
  const accessToken = signAccessToken(user);
  const refreshToken = await issueRefreshToken(user.id);
  return { accessToken, refreshToken };
}

function sanitize(user) {
  const { passwordHash, ...safe } = user;
  return safe;
}

module.exports = { register, login, rotateRefreshToken, signAccessToken };
```

## Step 3 — Auth + role middleware
```js
// src/middleware/authenticate.js
const jwt = require('jsonwebtoken');

function authenticate(req, res, next) {
  const header = req.headers.authorization;
  if (!header?.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Missing or malformed Authorization header' });
  }
  const token = header.slice(7);
  try {
    const payload = jwt.verify(token, process.env.JWT_ACCESS_SECRET);
    req.user = { id: payload.sub, role: payload.role, email: payload.email };
    next();
  } catch (err) {
    if (err.name === 'TokenExpiredError') {
      return res.status(401).json({ error: 'Access token expired', code: 'TOKEN_EXPIRED' });
    }
    return res.status(401).json({ error: 'Invalid token' });
  }
}

module.exports = authenticate;
```
```js
// src/middleware/authorize.js
function authorize(...allowedRoles) {
  return (req, res, next) => {
    if (!req.user) return res.status(401).json({ error: 'Not authenticated' });
    if (!allowedRoles.includes(req.user.role)) {
      return res.status(403).json({ error: 'Insufficient permissions' });
    }
    next();
  };
}

module.exports = authorize;
```

## Step 4 — Fine-grained permission helper (own vs any)
```js
// src/middleware/canAccessResource.js
// Use when USER can only act on their own records, but ADMIN/OWNER can act on any.
const prisma = require('../lib/prisma');

function canAccessResource(model, ownerField = 'userId') {
  return async (req, res, next) => {
    if (['ADMIN', 'OWNER'].includes(req.user.role)) return next();
    const record = await prisma[model].findUnique({ where: { id: req.params.id } });
    if (!record) return res.status(404).json({ error: 'Not found' });
    if (record[ownerField] !== req.user.id) {
      return res.status(403).json({ error: 'You do not own this resource' });
    }
    req.resource = record; // avoid a second DB hit in the controller
    next();
  };
}

module.exports = canAccessResource;
```

## Step 5 — Routes
```js
// src/routes/auth.routes.js
const router = require('express').Router();
const authService = require('../services/auth.service');
const authenticate = require('../middleware/authenticate');

router.post('/register', async (req, res, next) => {
  try {
    const user = await authService.register(req.body);
    res.status(201).json(user);
  } catch (err) { next(err); }
});

router.post('/login', async (req, res, next) => {
  try {
    const result = await authService.login(req.body);
    res.cookie('refreshToken', result.refreshToken, {
      httpOnly: true, secure: true, sameSite: 'strict', maxAge: 7 * 24 * 60 * 60 * 1000,
    });
    res.json({ user: result.user, accessToken: result.accessToken });
  } catch (err) { next(err); }
});

router.post('/refresh', async (req, res, next) => {
  try {
    const { accessToken, refreshToken } = await authService.rotateRefreshToken(req.cookies.refreshToken);
    res.cookie('refreshToken', refreshToken, { httpOnly: true, secure: true, sameSite: 'strict' });
    res.json({ accessToken });
  } catch (err) { next(err); }
});

router.get('/me', authenticate, (req, res) => res.json(req.user));

module.exports = router;
```
```js
// src/routes/booking.routes.js
const router = require('express').Router();
const authenticate = require('../middleware/authenticate');
const authorize = require('../middleware/authorize');
const canAccessResource = require('../middleware/canAccessResource');
const controller = require('../controllers/booking.controller');

router.get('/admin/reports', authenticate, authorize('ADMIN'), controller.getReports);
router.post('/bookings', authenticate, authorize('OWNER', 'USER'), controller.create);
router.delete('/bookings/:id', authenticate, canAccessResource('booking'), controller.cancel);

module.exports = router;
```

## Step 6 — React: AuthContext + ProtectedRoute
```jsx
// src/context/AuthContext.jsx
import { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/auth/me')
      .then(res => setUser(res.data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    localStorage.setItem('accessToken', data.accessToken);
    setUser(data.user);
  };

  const logout = () => {
    localStorage.removeItem('accessToken');
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
```
```jsx
// src/routes/ProtectedRoute.jsx
import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export function ProtectedRoute({ allowedRoles, children }) {
  const { user, loading } = useAuth();
  if (loading) return <FullPageSpinner />;
  if (!user) return <Navigate to="/login" replace />;
  if (allowedRoles && !allowedRoles.includes(user.role)) {
    return <Navigate to="/unauthorized" replace />;
  }
  return children;
}
```
```jsx
// App routing
<Route path="/admin" element={
  <ProtectedRoute allowedRoles={['ADMIN']}><AdminDashboard /></ProtectedRoute>
} />
<Route path="/owner" element={
  <ProtectedRoute allowedRoles={['OWNER']}><OwnerDashboard /></ProtectedRoute>
} />
```
Route each role to a **distinct dashboard component**, not one dashboard with conditionals — cleaner code and visibly clearer to judges skimming your repo.

## Enterprise best practices
- Access tokens short-lived (15 min), refresh tokens rotated on every use and stored `httpOnly` — never in `localStorage` (XSS-exposed). The example above stores refresh in a cookie and access token in memory/localStorage as a pragmatic hackathon tradeoff; call this out if asked.
- Never allow `role` to be client-supplied on register/update endpoints — always default to `USER` server-side.
- Log every failed authentication attempt (`console.warn` minimum) — judges may ask about brute-force protection; pair with the Rate Limiting skill.

## Checklist before moving on
- [ ] Public registration cannot create an `ADMIN` account
- [ ] Every protected route has both `authenticate` AND `authorize`/`canAccessResource`
- [ ] Frontend role check is UX only — backend is source of truth
- [ ] Refresh token rotates and old token is invalidated on use
- [ ] Passwords hashed with bcrypt cost ≥ 12, never logged

## Common mistakes to avoid
- Checking `req.user.role` in the controller instead of middleware — easy to forget on a new route added at hour 20. Always express it declaratively in the route definition.
- Trusting `req.body.role` on any create/update call.
- Putting the JWT secret in source control — use `.env` + `.env.example` with placeholder values.

## Integration with the rest of the stack
- The `role` on `req.user` is what the **State Machine skill** uses to decide which transitions a caller may trigger.
- `authenticate` populates `req.user.id`, which the **Immutable Audit Log** skill uses as `actor_id`.
- Socket connections (see **WebSocket Real-Time Sync**) reuse `JWT_ACCESS_SECRET` to authenticate the handshake — one secret, one source of truth.

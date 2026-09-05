# Input Validation Generator

## Why this matters
Odoo judges explicitly require absolute input validation and robust error handling — never trust user input. This is the criterion most likely to get silently skipped under time pressure because "it still works in the demo." A centralized, shared-schema approach means every endpoint gets it for free instead of relying on everyone remembering.

## Architecture
```
shared/schemas/*.schema.js   ← single source of truth, imported by BOTH sides
        │                    │
        ▼                    ▼
  Express middleware     React form hook
  (validate(schema))     (useValidatedForm(schema))
        │
        ▼
  standardized { error, code, details } response on failure
```
One schema file per domain entity, imported on both frontend and backend — you write the rule once, get both validations, and they can never drift apart.

## Step 1 — Shared schema (Zod, works in Node and browser bundles)
```js
// shared/schemas/booking.schema.js
const { z } = require('zod');

const createBookingSchema = z.object({
  resourceId: z.string().uuid(),
  start: z.string().datetime(),
  end: z.string().datetime(),
  notes: z.string().max(500).optional(),
}).refine(d => new Date(d.end) > new Date(d.start), {
  message: 'End time must be after start time',
  path: ['end'],
});

const transitionBookingSchema = z.object({
  status: z.enum(['CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED']),
});

module.exports = { createBookingSchema, transitionBookingSchema };
```
```js
// shared/schemas/user.schema.js
const { z } = require('zod');

const registerSchema = z.object({
  email: z.string().email().max(255),
  password: z.string().min(8).max(72)
    .regex(/[A-Z]/, 'Must contain an uppercase letter')
    .regex(/[0-9]/, 'Must contain a number'),
  name: z.string().min(1).max(120).trim(),
});

module.exports = { registerSchema };
```

## Step 2 — Express validation middleware
```js
// src/middleware/validate.js
function validate(schema, source = 'body') {
  return (req, res, next) => {
    const result = schema.safeParse(req[source]);
    if (!result.success) {
      return res.status(400).json({
        error: 'Validation failed',
        code: 'VALIDATION_ERROR',
        details: result.error.flatten().fieldErrors,
      });
    }
    req[source] = result.data; // parsed + coerced + stripped of unknown keys
    next();
  };
}

module.exports = validate;
```
```js
// usage
const validate = require('../middleware/validate');
const { createBookingSchema, transitionBookingSchema } = require('../../shared/schemas/booking.schema');

router.post('/bookings', authenticate, validate(createBookingSchema), controller.create);
router.patch('/bookings/:id/status', authenticate, validate(transitionBookingSchema), controller.transition);
```

## Step 3 — Standardized error envelope (global handler)
```js
// src/middleware/errorHandler.js
function errorHandler(err, req, res, next) {
  console.error(`[${new Date().toISOString()}] ${req.method} ${req.path} —`, err);

  const statusCode = err.statusCode || 500;
  const body = {
    error: statusCode === 500 ? 'Internal server error' : err.message,
    code: err.code || 'INTERNAL_ERROR',
  };
  if (err.details) body.details = err.details;

  res.status(statusCode).json(body); // never leak stack traces or raw DB errors
}

module.exports = errorHandler;
```
```js
// app.js — registered LAST, after all routes
app.use(errorHandler);
```

## Step 4 — Frontend: shared schema drives the form
```jsx
// src/hooks/useValidatedForm.js
import { useState } from 'react';

export function useValidatedForm(schema, initialValues) {
  const [values, setValues] = useState(initialValues);
  const [errors, setErrors] = useState({});

  const validate = () => {
    const result = schema.safeParse(values);
    if (!result.success) {
      setErrors(result.error.flatten().fieldErrors);
      return null;
    }
    setErrors({});
    return result.data;
  };

  const setField = (field, value) => setValues(v => ({ ...v, [field]: value }));

  return { values, errors, setField, validate };
}
```
```jsx
// src/components/BookingForm.jsx
import { createBookingSchema } from '../../shared/schemas/booking.schema';
import { useValidatedForm } from '../hooks/useValidatedForm';

function BookingForm({ onSubmit }) {
  const { values, errors, setField, validate } = useValidatedForm(createBookingSchema, {
    resourceId: '', start: '', end: '', notes: '',
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    const data = validate();
    if (!data) return; // errors populated, form re-renders with messages
    onSubmit(data);
  };

  return (
    <form onSubmit={handleSubmit}>
      <input value={values.start} onChange={e => setField('start', e.target.value)} type="datetime-local" />
      {errors.start && <span className="field-error">{errors.start[0]}</span>}
      {/* remaining fields follow the same pattern */}
      <button type="submit">Book</button>
    </form>
  );
}
```

## Enterprise best practices — per-endpoint checklist details
- **Type & bounds**: every string field has `.max()`, every number has `.int()`/`.positive()` where relevant.
- **Enums as allow-lists**: `status`, `role`, and any categorical field use `z.enum([...])`, never a raw string — this ties directly into the State Machine skill's `TRANSITIONS` map.
- **Foreign keys validated for existence**, not just UUID format — check in the service layer after schema validation passes:
```js
const resource = await prisma.resource.findUnique({ where: { id: data.resourceId } });
if (!resource) { const e = new Error('Resource not found'); e.statusCode = 404; throw e; }
```
- **No raw SQL string interpolation** — Prisma parameterizes automatically; if you must drop to `$queryRaw`, always use tagged-template interpolation (as shown in the State Machine skill), never string concatenation.
- **Stored-XSS prevention**: any free-text field that will be rendered back (e.g. `notes`) should be escaped on render (React does this by default via JSX — never use `dangerouslySetInnerHTML` on user content).

## Checklist before moving on
- [ ] Every POST/PATCH/PUT route has a `validate(schema)` middleware
- [ ] Schema file is imported from `shared/`, not duplicated on frontend and backend
- [ ] Global error handler is registered last and never leaks stack traces
- [ ] Foreign key IDs are existence-checked, not just format-checked
- [ ] Enum fields reject any value outside the defined set

## Common mistakes to avoid
- Validating only on the frontend "to save time" — trivially bypassed with curl/Postman; judges may probe this directly.
- Returning raw Zod error objects to the client — always flatten via `.flatten().fieldErrors` for a clean, frontend-consumable shape.
- Registering the error handler middleware before your routes (Express requires it last, with 4 arguments, to be recognized as an error handler).

## Integration with the rest of the stack
- `transitionBookingSchema`'s enum is kept in sync with `TRANSITIONS` in the **State Machine** skill — update both together.
- The `{ error, code, details }` envelope is what the **Rate Limiting** skill's `429` responses and the **Idempotent API Design** skill's conflict responses also follow, for one consistent error shape across the whole API.

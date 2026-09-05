# Search + Filtering Architecture

## Why this matters
Every dashboard a judge actually clicks around in lives or dies on this: can they filter bookings by status, search resources by name, sort by date, and not wait forever on page 1 of 10,000 rows? A generic, reusable query-building layer (not a bespoke `WHERE` clause per endpoint) is what makes every list view in your app feel consistent and "product-grade" instead of a raw table dump — and it's one of the most visibly-used features in a live demo.

## Architecture
```
Client sends: ?q=court+3&status=CONFIRMED&sort=-createdAt&page=2&pageSize=20
        │
        ▼
QueryParser — validates + normalizes into a structured query object
        │
        ▼
QueryBuilder — translates the structured object into a Prisma `where`/`orderBy`/`skip`/`take`
        │
        ├── Full-text search → Postgres `tsvector` column + GIN index (fast, native)
        ├── Structured filters → indexed equality/range Prisma where clauses
        ├── Sorting → allow-listed sortable fields only
        └── Pagination → cursor-based for large tables, offset-based for small/admin views
        │
        ▼
Response: { data, meta: { total, page, pageSize, hasMore } }
```
One reusable builder, parameterized per entity — write it once for `Booking`, reuse the same shape for `Resource`, `User`, `AuditLog`.

## Step 1 — Full-text search column (Postgres native, no external search engine needed)
```sql
-- prisma/migrations/xxxx_add_search_index/migration.sql
ALTER TABLE "Resource" ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(description, '')), 'B')
  ) STORED;

CREATE INDEX resource_search_idx ON "Resource" USING GIN (search_vector);
```
A generated, stored column keeps the search index automatically in sync on every insert/update — no application code has to remember to update it.

## Step 2 — Query parser (validates and normalizes request params)
```js
// src/query/queryParser.js
const { z } = require('zod');

function buildQuerySchema(sortableFields, filterableFields) {
  return z.object({
    q: z.string().max(200).optional(),
    sort: z.string().optional().refine(
      v => !v || sortableFields.includes(v.replace(/^-/, '')),
      { message: 'Invalid sort field' }
    ),
    page: z.coerce.number().int().min(1).default(1),
    pageSize: z.coerce.number().int().min(1).max(100).default(20),
    ...Object.fromEntries(filterableFields.map(f => [f, z.string().optional()])),
  });
}

module.exports = { buildQuerySchema };
```

## Step 3 — Generic query builder
```js
// src/query/queryBuilder.js
function buildPrismaQuery({ parsed, filterableFields, searchFields }) {
  const where = {};

  filterableFields.forEach(field => {
    if (parsed[field] !== undefined) {
      // support comma-separated multi-value filters: ?status=CONFIRMED,PENDING
      const values = parsed[field].split(',');
      where[field] = values.length > 1 ? { in: values } : values[0];
    }
  });

  let orderBy;
  if (parsed.sort) {
    const desc = parsed.sort.startsWith('-');
    const field = parsed.sort.replace(/^-/, '');
    orderBy = { [field]: desc ? 'desc' : 'asc' };
  } else {
    orderBy = { createdAt: 'desc' };
  }

  const skip = (parsed.page - 1) * parsed.pageSize;
  const take = parsed.pageSize;

  return { where, orderBy, skip, take };
}

module.exports = { buildPrismaQuery };
```

## Step 4 — Full-text search wired in (raw SQL for the tsvector match, Prisma for the rest)
```js
// src/services/resource.service.js
const prisma = require('../lib/prisma');
const { buildPrismaQuery } = require('../query/queryBuilder');

async function searchResources(parsed, tenantId) {
  const { where, orderBy, skip, take } = buildPrismaQuery({
    parsed,
    filterableFields: ['category', 'status'],
  });
  where.tenantId = tenantId;

  if (parsed.q) {
    // full-text match via raw query, then fetch the matching rows through Prisma for consistent shaping
    const matches = await prisma.$queryRaw`
      SELECT id FROM "Resource"
      WHERE "tenantId" = ${tenantId}
        AND search_vector @@ plainto_tsquery('english', ${parsed.q})
      ORDER BY ts_rank(search_vector, plainto_tsquery('english', ${parsed.q})) DESC
      LIMIT ${take} OFFSET ${skip};
    `;
    const ids = matches.map(m => m.id);
    where.id = { in: ids };
  }

  const [data, total] = await Promise.all([
    prisma.resource.findMany({ where, orderBy, skip: parsed.q ? 0 : skip, take: parsed.q ? undefined : take }),
    prisma.resource.count({ where }),
  ]);

  return {
    data,
    meta: {
      total, page: parsed.page, pageSize: parsed.pageSize,
      hasMore: parsed.page * parsed.pageSize < total,
    },
  };
}

module.exports = { searchResources };
```
When `q` is present, ranking (`ts_rank`) determines order — sort/pagination params are still honored for the non-search case, but relevance wins when the user is actively searching.

## Step 5 — Route wiring
```js
// src/routes/resource.routes.js
const { buildQuerySchema } = require('../query/queryParser');
const { searchResources } = require('../services/resource.service');

const resourceQuerySchema = buildQuerySchema(
  ['name', 'createdAt', 'category'],       // sortable
  ['category', 'status']                    // filterable (exact/multi-value)
);

router.get('/resources', authenticate, async (req, res, next) => {
  try {
    const parsed = resourceQuerySchema.parse(req.query);
    const result = await searchResources(parsed, req.user.tenantId);
    res.json(result);
  } catch (err) { next(err); }
});
```

## Step 6 — Cursor-based pagination for large tables (audit logs, high-volume booking history)
```js
// offset pagination degrades on large tables (OFFSET 50000 still scans 50000 rows) — use a cursor instead
async function listAuditLogCursor({ tenantId, cursor, take = 50 }) {
  const rows = await prisma.auditLog.findMany({
    where: { tenantId },
    orderBy: { createdAt: 'desc' },
    take: take + 1, // fetch one extra to know if there's a next page
    ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
  });

  const hasMore = rows.length > take;
  const data = hasMore ? rows.slice(0, take) : rows;
  return { data, nextCursor: hasMore ? data[data.length - 1].id : null };
}
```
Use offset pagination (Step 4) for admin/small tables where "jump to page 7" matters; use cursor pagination for anything that can grow past a few thousand rows, like audit logs.

## Step 7 — Saved filters
```prisma
model SavedFilter {
  id        String   @id @default(uuid())
  userId    String
  name      String
  entity    String   // 'bookings', 'resources'
  query     Json      // the raw query params object, replayed on load
  createdAt DateTime @default(now())
}
```
```js
router.post('/saved-filters', authenticate, async (req, res) => {
  const filter = await prisma.savedFilter.create({
    data: { userId: req.user.id, name: req.body.name, entity: req.body.entity, query: req.body.query },
  });
  res.status(201).json(filter);
});

router.get('/saved-filters/:entity', authenticate, async (req, res) => {
  const filters = await prisma.savedFilter.findMany({ where: { userId: req.user.id, entity: req.params.entity } });
  res.json(filters);
});
```

## Step 8 — Frontend: reusable filter bar + table
```jsx
// src/hooks/useListQuery.js
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

export function useListQuery(endpoint, initialQuery = { page: 1, pageSize: 20 }) {
  const [query, setQuery] = useState(initialQuery);

  const result = useQuery({
    queryKey: [endpoint, query],
    queryFn: () => api.get(endpoint, { params: query }).then(r => r.data),
    keepPreviousData: true, // avoids a loading flash when paging
  });

  const setFilter = (key, value) => setQuery(q => ({ ...q, [key]: value, page: 1 })); // filtering resets to page 1
  const setPage = (page) => setQuery(q => ({ ...q, page }));
  const setSort = (sort) => setQuery(q => ({ ...q, sort }));

  return { ...result, query, setFilter, setPage, setSort };
}
```
```jsx
function ResourceListPage() {
  const { data, isLoading, query, setFilter, setPage, setSort } = useListQuery('/resources');

  return (
    <div>
      <div className="flex gap-2 mb-4">
        <input placeholder="Search resources..." onChange={e => setFilter('q', e.target.value)} />
        <select onChange={e => setFilter('status', e.target.value)}>
          <option value="">All statuses</option>
          <option value="AVAILABLE">Available</option>
          <option value="MAINTENANCE">Maintenance</option>
        </select>
      </div>
      <Table data={data?.data} onSort={setSort} />
      <Pagination page={query.page} pageSize={query.pageSize} total={data?.meta.total} onPageChange={setPage} />
    </div>
  );
}
```

## Enterprise best practices
- Allow-list sortable/filterable fields explicitly (Step 2) — never accept an arbitrary `sort=` value and interpolate it into SQL/Prisma, that's an injection surface.
- Generated `tsvector` columns keep search indexes consistent automatically — don't maintain a separate search index by hand in application code.
- `keepPreviousData` (or equivalent) on the frontend avoids a jarring loading flash on every keystroke/page change — pair with debounced search input for a polished feel.

## Checklist before moving on
- [ ] Sortable and filterable fields are explicit allow-lists, not free-form
- [ ] Full-text search uses a native Postgres `tsvector` + GIN index, not a `LIKE '%...%'` scan
- [ ] Pagination response includes `total` and `hasMore` so the frontend can render page controls correctly
- [ ] Large/growing tables (audit log) use cursor pagination, not offset
- [ ] Search input is debounced on the frontend (250-400ms) to avoid firing a request per keystroke

## Common mistakes to avoid
- `WHERE name LIKE '%query%'` on a large table — no index can serve this efficiently; use the `tsvector` approach instead.
- Accepting `sort=password` or similarly unintended fields because the sort param wasn't validated against an allow-list.
- Offset pagination on a table with tens of thousands of rows — `OFFSET 9000 LIMIT 20` still scans and discards 9000 rows every time; switch to cursor pagination past a few thousand rows.

## Integration with the rest of the stack
- The `{ data, meta }` response shape composes cleanly with the **UI Component Kit**'s `AsyncBoundary` and table components.
- Filterable `status` fields are driven by the same enum as the **State Machine + Constraint Generator** skill.
- Saved filters and search activity are natural candidates for logging via the **Immutable Audit Log Generator** if you want usage analytics.

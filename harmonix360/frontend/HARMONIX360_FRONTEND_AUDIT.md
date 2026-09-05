# Harmonix360 Frontend Audit

Scope: `harmonix360/frontend/src/` (`lib/api-client.ts`, `routes/AssetsPage.tsx`, `main.tsx`, `router.tsx`) and build configuration (`package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`).

Legend:
- **CORE** = generic and entity-agnostic infrastructure ready for reuse across any Product Surface (PS).
- **DOMAIN** = Asset/Transfer-specific implementation tied directly to a single vertical slice.
- **HYBRID** = generic shape or wrapper, but with hardcoded endpoints, types, or inline calls.
- **MISSING** = functionality does not exist in any form in the frontend codebase.

---

## 1. Existing Pages & Vertical Slice Coverage

**Classification: DOMAIN (Single hand-built Asset list page; Transfer slice is 100% MISSING).**

Evidence:
- `src/routes/AssetsPage.tsx:18-86`: Only a single page component exists: `AssetsPage`. It displays an Asset list table hand-built using primitive HTML table elements (`<table>`, `<thead>`, `<tbody>`, `<tr>`, `<td>`, lines 48-81) with hardcoded column headers (`Public ID (Hashid)`, `Asset Name`, `Asset Tag`, `Status`, `Condition`, lines 51-55).
- No detail page exists (e.g. `AssetDetail.tsx` or route `/assets/:id`).
- No creation or edit modal/page exists. The `+ Add New Asset` button (`src/routes/AssetsPage.tsx:31-33`) is an unclickable static `<button>` element with no `onClick` handler, state hook, or modal trigger.
- **Transfer slice is MISSING:** Despite the backend having a full `TransferRequest` vertical slice (`app/services/transfer.py`, `app/api/v1/routers/transfers.py`), there are zero pages, components, types, or API calls for Transfers in `src/`.
- `src/router.tsx:4-13`: The application router configures only two routes (`/` and `/assets`), both rendering `<AssetsPage />`:
  ```tsx
  const router = createBrowserRouter([
    { path: '/', element: <AssetsPage /> },
    { path: '/assets', element: <AssetsPage /> },
  ]);
  ```
- **No List/Detail/Form Pattern:** No generic list views (`ListView`), data tables (`DataTable`), detail cards (`DetailView`), or dialog/form abstractions exist. Every table row and column is hand-coded inside `AssetsPage.tsx`.

What would need to change:
Create reusable UI pattern templates: a generic `DataTable<T>` component with pagination/sorting/filtering, entity detail views (`EntityDetail`), and modal/drawer primitives. Implement the missing Transfer list, detail, and approval/creation pages.

---

## 2. API Layer & TanStack Query Integration

**Classification: HYBRID — Reusable HTTP client wrapper exists (CORE-ish), but TanStack Query usage is entirely ad-hoc and un-factored (DOMAIN).**

Evidence:
- `src/lib/api-client.ts:3-25`: `fetchApi<T>(endpoint, options)` provides a central wrapper over native `fetch`:
  - Automatically prepends `const API_BASE = '/api/v1'` (line 1).
  - Reads `access_token` from `localStorage.getItem('access_token')` (line 4) and sets `Authorization: Bearer ${token}` header (lines 10-12).
  - Parses JSON error payloads (`errorData.detail`, lines 19-22) and throws standard `Error` objects on non-2xx HTTP responses.
  - *Genericity:* The `fetchApi` function itself is clean and entity-agnostic.
- `src/main.tsx:5,8-10`: TanStack Query is initialized globally via `const queryClient = new QueryClient()` and `<QueryClientProvider client={queryClient}>`.
- **No Query Hooks or Key Factory:** There are no custom hooks (e.g., `useAssets()`, `useAsset(id)`, `useTransfers()`), no centralized query key management (e.g., `assetKeys.list()`), and no domain API modules (e.g., `src/api/assets.ts`).
- **Inline Ad-Hoc Invocation:** `src/routes/AssetsPage.tsx:19-22` invokes `useQuery` directly inside the page component with hardcoded array string keys and inline `fetchApi` closures:
  ```tsx
  const { data, isLoading, error } = useQuery<PaginatedAssets>({
    queryKey: ['assets'],
    queryFn: () => fetchApi<PaginatedAssets>('/assets/'),
  });
  ```

What would need to change:
Extract domain API functions out of page components into dedicated API modules (`src/api/assets.ts`, `src/api/transfers.ts`). Implement a structured query-key factory pattern (`src/lib/query-keys.ts`) and wrap endpoints in custom React Query hooks (`useAssets()`, `useAsset()`, `useCreateAsset()`, `useTransfers()`, `useApproveTransfer()`).

---

## 3. Authentication & Authorization

**Classification: MISSING — No auth context, current user state, login interface, or route guarding.**

Evidence:
- Token handling is purely passive inside `src/lib/api-client.ts:4` (`localStorage.getItem('access_token')`).
- No React Context or state store (`AuthContext`, `useAuth`) exists to manage user login state, stored JWTs, user identity, or roles.
- No current user profile representation exists (backend `User` model, `UserRole` enum `EMPLOYEE` / `ASSET_MANAGER` / `DEPARTMENT_HEAD` / `ADMIN`).
- No route protection components exist (`ProtectedRoute`, `RoleGuard`, `RequireAuth`). `src/router.tsx:4-13` renders routes unconditionally without checking authentication status or role permissions.
- No Login page (`/login`) or logout functionality exists, despite the backend exposing POST `/api/v1/auth/login` (`app/api/v1/routers/auth.py:11-20`).

What would need to change:
Build an `AuthProvider` context and `useAuth()` hook that manages login/logout lifecycle, stores tokens in `localStorage`, decodes/fetches the current user profile, and provides role-checking helpers (`hasRole(...)`). Wrap routes in a `ProtectedRoute` component to enforce role-based access control.

---

## 4. Form System & Validation

**Classification: MISSING — Zero form libraries, validation schemas, error handling, or submit abstractions.**

Evidence:
- There are no form components, form state management hooks, or validation libraries (e.g., `react-hook-form`, `zod`, `formik`, `yup`) anywhere in `src/`.
- No reusable form field components exist (e.g., `FormField`, `TextInput`, `SelectInput`, `FormError`).
- The "+ Add New Asset" button (`src/routes/AssetsPage.tsx:31-33`) is non-functional markup.
- No mutation hooks (`useMutation`) exist for creating, updating, or deleting entities or executing actions (such as submitting or approving transfer requests).

What would need to change:
Install and configure a form state & validation pipeline (`react-hook-form` + `zod`). Create reusable form controls and error-display components integrated with Shadcn UI primitives.

---

## 5. Layout & Navigation Shell

**Classification: MISSING — No shared layout shell, sidebar, top bar, or nested routing structure.**

Evidence:
- No shared shell or layout component (`AppShell`, `DashboardLayout`, `Sidebar`, `Header`) exists in `src/`.
- `src/router.tsx:4-13` maps routes directly to root components (`element: <AssetsPage />`) rather than using layout routes with React Router `<Outlet />`.
- `src/routes/AssetsPage.tsx:25-34` hand-codes its own page container (`<div className="p-8 max-w-7xl mx-auto">`) and page header (`<h1 className="text-3xl font-bold text-slate-100">Asset Inventory</h1>`).
- Adding any new page would currently require copy-pasting the full page wrapper, dark background classes, title formatting, and container padding.

What would need to change:
Create an `AppShell` component containing a responsive `Sidebar` navigation, `Header` (with current user profile and tenant/role badge), main content viewport (`<Outlet />`), and breadcrumbs. Re-structure `src/router.tsx` to use layout nesting.

---

## 6. UI Component Library & Design System

**Classification: HYBRID / MISSING — Tailwind CSS v3 is installed and used via inline utility classes, but Shadcn UI components and global CSS entrypoints are MISSING.**

Evidence:
- **Tailwind Dependencies:** `package.json:16-18,26-28` includes `lucide-react`, `clsx`, `tailwind-merge`, `tailwindcss`, `autoprefixer`, and `postcss`.
- **Path Aliases Configured:** `vite.config.ts:8-10` and `tsconfig.json:19-21` configure `@/*` pointing to `src/*`.
- **Raw Tailwind Usage:** `src/routes/AssetsPage.tsx` uses raw inline Tailwind utility classes (e.g., `className="p-8 max-w-7xl mx-auto"`, `className="bg-slate-900 border border-slate-800 rounded-xl..."`, lines 25, 47).
- **Missing Global CSS Entrypoint:** There is no `index.css` or `globals.css` file in `src/` containing `@tailwind base; @tailwind components; @tailwind utilities;`. Without this file, Vite cannot inject Tailwind styles into the DOM.
- **Shadcn UI Missing:** There is no `@/components/ui/` directory, no Shadcn configuration (`components.json`), no `@/lib/utils.ts` (defining `cn()`), and zero primitive components (e.g., `Button`, `Table`, `Dialog`, `Input`, `Card`, `Badge`, `DropdownMenu`).

What would need to change:
Add `src/index.css` with Tailwind directives and import it in `src/main.tsx`. Add `@/lib/utils.ts` with `cn()` utility. Install and set up Shadcn UI component primitives (`button`, `table`, `dialog`, `input`, `badge`, `card`, `dropdown-menu`, `toast`).

---

## 7. Type System & Schema Alignment

**Classification: DOMAIN / MISSING — Local hand-written interfaces; no shared type library or backend Pydantic schema synchronization.**

Evidence:
- `src/routes/AssetsPage.tsx:4-16` defines inline TypeScript interfaces local to `AssetsPage.tsx`:
  ```tsx
  interface Asset {
    id: string;
    name: string;
    asset_tag: string;
    status: string;
    condition: string;
    location?: string;
  }

  interface PaginatedAssets {
    items: Asset[];
    total: number;
  }
  ```
- **No Shared Types Directory:** There is no `src/types/` folder or shared type definitions.
- **Incomplete Entity Representation:** The inline `Asset` interface omits essential fields present in backend Pydantic schemas (`app/schemas/asset.py:7-40`), such as `purchase_cost`, `purchase_date`, `serial_number`, `is_bookable`, `tenant_id`, `created_at`, `updated_at`, and `version`.
- **Missing Domain Types:** No TypeScript types exist for `TransferRequest` (`app/schemas/transfer.py`), `User` / `UserRole`, `AuditLog`, `ResourceBooking`, or standard API error responses (`HTTPValidationError`).
- **No Contract Generation:** No automated tool (e.g., `openapi-typescript`) is configured to sync frontend types with FastAPI OpenAPI definitions.

What would need to change:
Establish `src/types/` directory with structured type files (`entity.ts`, `asset.ts`, `transfer.ts`, `auth.ts`, `api.ts`). Reflect backend Pydantic models accurately and type API request/response payloads strictly.

---

## Prioritized Summary — Scaffold & Genericization Targets

The frontend is currently a minimal 4-file prototype (142 lines of code in `src/`) consisting of a single fetch wrapper and a single hardcoded Asset table page. Almost all core enterprise application infrastructure is **MISSING**.

To prepare the frontend for any Product Surface (PS) and multi-entity expansion, work should be prioritized in the following order:

1. **Global Design System & Shadcn Primitives** (`src/index.css`, `src/lib/utils.ts`, `src/components/ui/*`)
   Add the missing CSS entrypoint to enable Tailwind styling. Set up Shadcn UI primitives (`Button`, `Table`, `Dialog`, `Input`, `Badge`, `Card`, `Select`) so all pages share consistent, accessible styling.

2. **Shared Application Layout Shell** (`src/components/layout/AppShell.tsx`, `Sidebar.tsx`, `Header.tsx`)
   Extract common page wrapper logic into a generic navigation shell with route outlet, header, sidebar, and breadcrumbs, eliminating duplicate layout code.

3. **Authentication & Role-Based Guarding** (`src/context/AuthContext.tsx`, `src/components/auth/ProtectedRoute.tsx`, `src/routes/LoginPage.tsx`)
   Create auth context for token management, current user role state, login flow, and role-gated route guards.

4. **Shared Types & Domain API Layer** (`src/types/*`, `src/api/*`, `src/hooks/*`)
   Define strict TypeScript interfaces matching backend Pydantic schemas. Build API client modules and custom React Query hooks (`useAssets`, `useTransfers`, `useMutations`) with centralized query keys.

5. **Reusable Generic Components & Form Abstraction** (`src/components/common/DataTable.tsx`, `FormModal.tsx`)
   Build a generic `DataTable<T>` component with pagination, column definitions, and status badges, plus a standard form rendering pipeline using `react-hook-form` + `zod`.

6. **Complete Domain Page Implementations** (`src/routes/assets/*`, `src/routes/transfers/*`)
   Build missing Asset detail/create views and the complete Transfer workflow interface (transfer request list, creation drawer, AI decision evaluation & human override panel).

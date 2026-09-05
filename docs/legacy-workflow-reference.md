# Legacy Workflow Reference Documentation

Extracted from `ValidationProjects/AssetFlow/apps/api/src/assetflow/workflows/` before deletion.

## 1. Asset Lifecycle (`asset-lifecycle`)
- **Initial State**: `AVAILABLE`
- **States**: `AVAILABLE`, `ALLOCATED`, `RESERVED`, `MAINTENANCE`, `LOST`, `RETIRED`, `DISPOSED` (Terminal)
- **Transitions**:
  - `ALLOCATE`: `AVAILABLE` -> `ALLOCATED` (Roles: `ASSET_MANAGER`, `ADMIN`)
  - `RETURN`: `ALLOCATED` -> `AVAILABLE` (Roles: `EMPLOYEE`, `ASSET_MANAGER`, `ADMIN`)
  - `MAINTENANCE`: `['AVAILABLE', 'ALLOCATED']` -> `MAINTENANCE` (Roles: `ASSET_MANAGER`, `ADMIN`)
  - `RESTORE`: `MAINTENANCE` -> `AVAILABLE` (Roles: `ASSET_MANAGER`, `ADMIN`)
  - `RETIRE`: `AVAILABLE` -> `RETIRED` (Roles: `ADMIN`)
  - `DISPOSE`: `RETIRED` -> `DISPOSED` (Roles: `ADMIN`)
  - `MARK_LOST`: `['AVAILABLE', 'ALLOCATED']` -> `LOST` (Roles: `ADMIN`)
  - `MARK_FOUND`: `LOST` -> `AVAILABLE` (Roles: `ADMIN`, `ASSET_MANAGER`)

---

## 2. Transfer Request Lifecycle (`transfer-lifecycle`)
- **Initial State**: `PENDING`
- **States**: `PENDING`, `REJECTED` (Terminal), `COMPLETED` (Terminal)
- **Transitions**:
  - `APPROVE`: `PENDING` -> `COMPLETED` (Roles: `ASSET_MANAGER`, `DEPARTMENT_HEAD`, `ADMIN`)
    - *Note*: On approve, reallocate asset + transfer -> COMPLETED in single transaction.
  - `REJECT`: `PENDING` -> `REJECTED` (Roles: `ASSET_MANAGER`, `DEPARTMENT_HEAD`, `ADMIN`)

---

## 3. Resource Booking Lifecycle (`booking-lifecycle`)
- **Initial State**: `PENDING`
- **States**: `PENDING`, `CONFIRMED`, `CANCELLED` (Terminal), `COMPLETED` (Terminal)
- **Transitions**:
  - `CONFIRM`: `PENDING` -> `CONFIRMED` (Roles: `ASSET_MANAGER`, `DEPARTMENT_HEAD`, `ADMIN`)
  - `CANCEL`: `['PENDING', 'CONFIRMED']` -> `CANCELLED` (Roles: `EMPLOYEE`, `ASSET_MANAGER`, `DEPARTMENT_HEAD`, `ADMIN`)
  - `COMPLETE`: `CONFIRMED` -> `COMPLETED` (Roles: `ASSET_MANAGER`, `DEPARTMENT_HEAD`, `ADMIN`)

---

## 4. Maintenance Request Lifecycle (`maintenance-lifecycle`)
- **Initial State**: `PENDING`
- **States**: `PENDING`, `APPROVED`, `REJECTED` (Terminal), `IN_PROGRESS`, `COMPLETED` (Terminal)
- **Transitions**:
  - `APPROVE`: `PENDING` -> `APPROVED` (Roles: `ASSET_MANAGER`, `ADMIN`)
  - `REJECT`: `PENDING` -> `REJECTED` (Roles: `ASSET_MANAGER`, `ADMIN`)
  - `START`: `APPROVED` -> `IN_PROGRESS` (Roles: `ASSET_MANAGER`, `ADMIN`)
  - `COMPLETE`: `IN_PROGRESS` -> `COMPLETED` (Roles: `ASSET_MANAGER`, `ADMIN`)

---

## 5. Audit Cycle & Audit Log Lifecycles (`audit-cycle-lifecycle` & `audit-log-lifecycle`)
### Audit Cycle
- **Initial State**: `PLANNED`
- **States**: `PLANNED`, `IN_PROGRESS`, `COMPLETED` (Terminal)
- **Transitions**:
  - `START`: `PLANNED` -> `IN_PROGRESS` (Roles: `ADMIN`)
  - `COMPLETE`: `IN_PROGRESS` -> `COMPLETED` (Roles: `ADMIN`)

### Audit Asset Log
- **Initial State**: `PENDING`
- **States**: `PENDING`, `VERIFIED` (Terminal), `MISSING` (Terminal), `DAMAGED` (Terminal)
- **Transitions**:
  - `VERIFY`: `PENDING` -> `VERIFIED` (Roles: All authenticated)
  - `MARK_MISSING`: `PENDING` -> `MISSING` (Roles: All authenticated)
  - `MARK_DAMAGED`: `PENDING` -> `DAMAGED` (Roles: All authenticated)

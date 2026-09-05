# Harmonix360 Frontend

This directory contains the foundational frontend tooling and UI architecture for Harmonix360:

- Tailwind CSS + Shadcn UI setup (`src/index.css`, `tailwind.config.js`, `postcss.config.js`, `components.json`, `src/components/ui/*`)
- `cn()` class-merging utility (`src/lib/utils.ts`)
- A generic fetch client with auth-header injection and error parsing (`src/lib/api-client.ts`)
- Generic types shared across domains: `PaginatedResponse<T>`, `ResponseEnvelope<T>`, `HTTPValidationError`, `UserResponse`/`Token` (`src/types/common.ts`, `src/types/user.ts`, `src/types/enums.ts`)
- Application shell with sidebar/header/routing mechanics (`src/components/layout/AppShell.tsx`, `src/router.tsx`)
- Offline-first IndexedDB synchronization engine (`src/lib/offline-db.ts`, `src/lib/sync-engine.ts`)

Domain views and modules build directly on top of this standardized foundation.

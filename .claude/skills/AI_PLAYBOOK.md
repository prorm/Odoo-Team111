# AI Developer Constitution (AI_PLAYBOOK.md)

**STOP.** If you are an AI Coding Agent (Claude, Antigravity, Codex, Cursor, etc.), you MUST read and internalize this document before generating, modifying, or deleting any code.

This is the governing constitution for the Odoo Hackathon Framework. Four human developers and four different AI models will operate concurrently in this repository. Strict adherence to this playbook ensures our code compiles, integrates, and functions without conflicts.

For specific implementation details (e.g., how validation works), refer to the files inside the `Odoo Skills/` directory. Do not summarize or rewrite those skills. Build upon them using the governance rules below.

---

## 1. Vision of the Framework
This is an AI-assisted software factory. We are building a headless ERP Starter Framework. The architecture is modular and immutable at its core, but highly flexible at its domain boundaries. 

## 2. AI Agent Responsibilities
- **Do not invent patterns:** Use the established patterns in `odoo-skills/`.
- **Assume concurrency:** Assume another AI is editing a different module right now. Do not create global side-effects.
- **Fail fast:** If a requested task violates this playbook, explicitly refuse and cite the rule broken.
- **Do not hallucinate imports:** Only import what exists. 

## 3. Repository Philosophy
Strict boundaries. Domain logic (e.g., `fleet`, `assets`) never touches other domain logic directly. All communication happens through shared core packages and event buses.

## 4. Folder Ownership Rules
- `/packages/core`: Immutable core logic. 
- `/packages/database`: Immutable base schemas and connection logic.
- `/packages/ui`: Design system. Additions require human review.
- `/modules/{domain}`: **Your playground.** You have full read/write autonomy here.
- `Odoo Skills/`: Read-only. The source of truth for implementation patterns.

## 5. Files AI May Create
- New modules: `/modules/new-domain/`
- Domain schemas: `/packages/database/schema/new-domain.ts`
- Domain APIs: `/apps/api/routes/new-domain.ts`
- Domain UI: `/apps/web/features/new-domain/`

## 6. Files AI Must NEVER Modify
- Configuration files (`tsconfig.json`, `package.json`, `.eslintrc`, `docker-compose.yml`)
- Core authentication strategies (`/packages/core/auth`)
- `Odoo Skills/*` markdown files
**WHY:** Altering these breaks the environment for all other agents and humans instantly.

## 7. Coding Standards
- **Strict Typing:** No `any`. No `@ts-ignore`. 
- **Pure Functions:** Business logic must not have side effects.
- **GOOD:** `const calculateTotal = (items: InvoiceItem[]): number => items.reduce(...)`
- **BAD:** `const calculateTotal = () => { globalState.total = items.reduce(...) }`
- **WHY:** Pure functions are testable and deterministic.

## 8. Naming Conventions
- **Interfaces/Types:** PascalCase, no `I` prefix (e.g., `Asset`, not `IAsset`).
- **Files:** Kebab-case (e.g., `asset-controller.ts`).
- **Variables/Functions:** CamelCase (e.g., `getAssetById`).
- **Constants:** UPPER_SNAKE_CASE (e.g., `MAX_RETRY_COUNT`).

## 9. API Response Standards
Always use the standard JSON response wrapper.
- **GOOD:** `return res.status(200).json({ success: true, data: asset, error: null })`
- **BAD:** `return res.status(200).json(asset)`
- **WHY:** Uniform responses allow the frontend to use a single generic API client.

## 10. Error Handling Standards
Never throw raw errors to the client. Always wrap them in a standard ApplicationError.
- **GOOD:** `throw new NotFoundError("Asset not found", { assetId })`
- **BAD:** `throw new Error("Failed to get asset")`
- **WHY:** Raw errors leak stack traces and lack HTTP status code mappings.

## 11. Logging Standards
Use the core logger, never `console.log`.
- **GOOD:** `logger.info("Asset created", { assetId, tenantId })`
- **BAD:** `console.log("Created asset: " + assetId)`
- **WHY:** `console.log` cannot be indexed, lacks tenant context, and blocks the event loop in high volumes.

## 12. Validation Standards
All API inputs MUST be validated via Zod schemas defined in the domain module.
- **GOOD:** `const payload = CreateAssetSchema.parse(req.body)`
- **BAD:** `const payload = req.body as CreateAssetDTO`
- **WHY:** Type casting does not perform runtime checks, leading to SQL injection or null pointer exceptions.

## 13. State Machine Rules
Domain entities must change states only through the state machine engine, never by direct assignment.
- **GOOD:** `await stateMachine.transition(asset, 'APPROVE')`
- **BAD:** `asset.status = 'APPROVED'; await asset.save()`
- **WHY:** Direct assignment bypasses audit logs, transition guards, and webhooks.

## 14. Database Rules
Never execute raw SQL unless generating complex analytical reports. Use the ORM (Prisma/Drizzle).
- **GOOD:** `db.asset.findMany({ where: { tenantId } })`
- **BAD:** `db.query("SELECT * FROM assets")`
- **WHY:** Raw SQL bypasses Row-Level Security (RLS) and multi-tenant constraints.

## 15. Prisma Conventions
Every Prisma model MUST include `tenantId`, `createdAt`, `updatedAt`, and `deletedAt` (for soft deletes).
- **GOOD:** `model Asset { ... tenantId String ... deletedAt DateTime? }`
- **BAD:** `model Asset { id String, name String }`
- **WHY:** Multi-tenancy and auditability are non-negotiable hackathon requirements.

## 16. React Conventions
Use functional components and hooks. Extract complex state into Zustand or React Query.
- **GOOD:** `const { data: assets } = useQuery(['assets'], fetchAssets)`
- **BAD:** `useEffect(() => { fetchAssets().then(setAssets) }, [])`
- **WHY:** `useEffect` fetching lacks caching, deduplication, and retry logic.

## 17. Express/API Conventions
Keep controllers thin. They only handle HTTP parsing, validation, and passing data to services.
- **GOOD:** `const asset = await assetService.create(req.body); res.json(asset);`
- **BAD:** *(Putting 200 lines of database logic inside the route handler)*
- **WHY:** Thin controllers allow services to be unit tested without mocking HTTP request objects.

## 18. Import Conventions
Use absolute path aliases (`@/modules/...`, `@/packages/...`), never relative paths climbing out of directories.
- **GOOD:** `import { Button } from '@/packages/ui/Button'`
- **BAD:** `import { Button } from '../../../../packages/ui/Button'`
- **WHY:** Relative paths break when files are refactored or moved.

## 19. Git Workflow
AI agents must assume they are generating code that will be committed to isolated feature branches. Do not generate code that modifies multiple domains simultaneously.

## 20. Commit Message Format
Conventional Commits only.
- **GOOD:** `feat(fleet): add vehicle inspection state machine`
- **BAD:** `added inspection stuff`
- **WHY:** Automated changelogs and release notes rely on conventional commits.

## 21. Branch Strategy
Assume the strategy is `feature/{domain}/{feature-name}`. E.g., `feature/assets/qr-scanning`.

## 22. Definition of Done
Code is not "done" until:
1. It passes TypeScript compilation.
2. Zod schemas exist.
3. Multi-tenant checks are enforced.
4. UI matches the design system.

## 23. Pull Request Checklist
Did you: Use standard UI? Validate inputs? scope to Tenant? Log appropriately?

## 24. Integration Checklist
Ensure domain events are fired (e.g., `asset.created`) so other modules can react without tight coupling.

## 25. Testing Requirements
For hackathons, focus on integration tests over unit tests. Test the critical path (API -> Service -> DB).

## 26. Performance Guidelines
Paginate all list endpoints. Limit default fetches to 50 rows.
- **GOOD:** `GET /assets?limit=50&offset=0`
- **BAD:** `GET /assets` (returning 10,000 rows)
- **WHY:** Unbounded queries crash Node.js via Out-Of-Memory errors.

## 27. Security Requirements
Do not log API keys, user passwords, or tokens. Never inject unsanitized variables into HTML or SQL.

## 28. Multi-Tenant Rules
Every single database read/write MUST be scoped by `tenantId`.
- **GOOD:** `db.asset.update({ where: { id, tenantId }, data })`
- **BAD:** `db.asset.update({ where: { id }, data })`
- **WHY:** Cross-tenant data leaks mean instant disqualification.

## 29. AI Decision Guidelines
AI tasks (e.g., auto-categorizing an asset) must run in background jobs, NOT in the HTTP request cycle.
- **GOOD:** `await queue.add('ai-categorize', { assetId })`
- **BAD:** `const category = await openai.createCompletion(...); return res.json(...)`
- **WHY:** LLM API calls take 2-10 seconds. HTTP endpoints will timeout and block the event loop.

## 30. Audit Requirements
All entity mutations (Create, Update, Delete) must trigger the audit log service asynchronously.

## 31. Feature Flag Rules
Wrap new experimental hackathon features in flags.
- **GOOD:** `if (flags.isEnabled('AI_ASSET_TAGGING')) { ... }`
- **BAD:** Hardcoding experimental, unstable logic into the main branch execution path.
- **WHY:** If a feature fails during a demo, we can turn it off via a GUI rather than redeploying.

## 32. Notification Rules
Do not await notification delivery (emails/SMS). Fire an event and let the Notification Engine handle it.

## 33. Background Job Rules
All jobs must be idempotent. If a job fails and retries, it must not create duplicate side-effects.

## 34. Offline Rules
Frontend mutations must use optimistic updates and write to IndexedDB before attempting network requests.

## 35. WebSocket Rules
Push minimal payloads. Push an `{ entity: 'Asset', id: '123', action: 'UPDATE' }` and let the client refetch, rather than pushing the entire object.

## 36. UI Consistency Rules
Do not invent new CSS classes or colors. Use predefined Tailwind tokens from `@/packages/ui`.
- **GOOD:** `<div className="text-primary bg-surface-1">`
- **BAD:** `<div style={{ color: '#ff0000', backgroundColor: '#f5f5f5' }}>`
- **WHY:** Hardcoded colors break Dark Mode and tenant theming.

## 37. Reusability Rules
If you write logic that applies to multiple domains (e.g., exporting a table to CSV), put it in `@/packages/core`, not the domain module.

## 38. Forbidden Patterns
- **No Global Variables.**
- **No inline styles.**
- **No default exports** (use named exports for better refactoring and intellisense).
- **No mocking core services** to "get things to work." Fix the implementation.

## 39. Common AI Mistakes
- **Mistake:** Assuming the database automatically scopes to tenant. **Correction:** You must manually include `tenantId` in every query where RLS isn't strictly enforced.
- **Mistake:** Generating massive monolithic files. **Correction:** Break logic into small files: `routes.ts`, `controller.ts`, `service.ts`, `schema.ts`.
- **Mistake:** Using `npm install` for random dependencies. **Correction:** Only use existing packages in the monorepo to avoid bloat and conflicting versions.

## 40. Hackathon-Specific Best Practices
- **Prioritize the "Happy Path":** Ensure the core demo flow works perfectly before adding edge-case validations.
- **Mock Heavy Integrations:** If an external API is slow or requires complex setup, create a dummy implementation in the service layer that returns realistic static data.
- **Speed Over Purity (but follow boundaries):** If you must hack something, do it entirely inside the `/modules/{domain}` folder so the core architecture remains uncontaminated.

---
## 41. AI Agent Communication Protocol (Handoff)
Every completed task MUST end with a strict Handoff block so the next AI agent (or human) knows exactly what to do without guessing.

**Format:**
```text
Completed:
- [List of files created]
- [List of files modified]

Exports:
- [Routes created]
- [Types/Interfaces exported]
- [Components available]

Requires Next:
- [e.g., Validation, Integration, Tests]

Known Limitations:
- [e.g., Edge case XYZ not handled]
```
**WHY:** If the Backend Agent finishes, the Frontend Agent instantly knows which endpoints and types exist.

## 42. AI Context Budget Rules ⭐
Never give an AI the entire repository or the entire skills folder. Context fills up, performance drops, and the AI loses memory.
- **GOOD:** Only provide the *Relevant Skill*, *Relevant Module*, *Relevant Folder*, and *Relevant Interfaces*.
- **BAD:** Uploading all 50 files in the repo for a 2-line change.
- **WHY:** Saves thousands of tokens, keeps the AI focused, and prevents hallucinated cross-contamination.

## 43. AI Review Loop
Never merge AI-generated code directly. Every feature must go through this strict loop:
`Builder AI` ➔ `Reviewer AI` ➔ `Integration AI` ➔ `Human`
- **Builder AI:** Writes the initial code.
- **Reviewer AI:** Checks against this playbook and Odoo skills.
- **Integration AI:** Ensures it wires up correctly with existing modules.
- **Human:** Final sign-off.
**WHY:** Catches architectural violations before they bleed into the main branch.

## 44. Build Verification
Before ANY commit, the AI or the Human operator must run the following checks. If ANY fail, do NOT commit.
```bash
npm run lint
npm run typecheck
npm run build
```
**WHY:** Prevents broken code from being pushed to the collaborative branch and crashing other agents' environments.

## 45. Prompt Versioning
Treat prompts as code. They must be versioned.
Store prompts in `/prompts/v1/`, `/prompts/v2/`, etc.
- **GOOD:** `Run prompt /prompts/v3/generate-asset.md`
- **BAD:** Re-typing the prompt from memory.
- **WHY:** If Prompt v4 fails, we analyze why, improve it to v5, and eventually reach a 95% success rate. History is crucial.

## 46. Skill Dependency Graph
Understand the build order. Skills are not isolated; they build on each other. Do not attempt to build a downstream skill before its upstream dependencies exist.
**Build Order:**
`Validation` ➔ `State Machine` ➔ `Audit` ➔ `Notifications` ➔ `Realtime` ➔ `Reports`
**WHY:** You cannot audit an entity that doesn't have a state machine, and you cannot notify on an event that wasn't audited.

## 47. AI Output Format
Every AI response (even intermediate ones) should finish with this summary:
```text
SUMMARY
Files Created: [...]
Files Modified: [...]
Dependencies Added: [...]
Commands to Run: [...]
Potential Risks: [...]
Next Prompt Recommendation: [...]
```
**WHY:** Makes handoffs incredible and provides humans with an exact map of the AI's actions.

## 48. Odoo Skills Index

The `Odoo Skills/` directory contains strict implementation guidelines and AI skills for the following areas:

- **01. Git Workflow Enforcer** (`01-git-workflow-enforcer.skill`)
- **01. Role Based Auth Scaffolder** (`01-role-based-auth-scaffolder.md`)
- **02. Role Based Auth Scaffolder** (`02-role-based-auth-scaffolder.skill`)
- **02. State Machine Constraint Generator** (`02-state-machine-constraint-generator.md`)
- **03. Input Validation Generator** (`03-input-validation-generator.md`)
- **03. State Machine Constraint Generator** (`03-state-machine-constraint-generator.skill`)
- **04. Input Validation Generator** (`04-input-validation-generator.skill`)
- **04. UI Component Kit** (`04-ui-component-kit.md`)
- **05. UI Component Kit** (`05-ui-component-kit.skill`)
- **05. Websocket Realtime Sync** (`05-websocket-realtime-sync.md`)
- **06. Demo Script Builder** (`06-demo-script-builder.md`)
- **06. Websocket Realtime Sync** (`06-websocket-realtime-sync.skill`)
- **07. Demo Script Builder** (`07-demo-script-builder.skill`)
- **07. Multi Tenant Data Isolation** (`07-multi-tenant-data-isolation.md`)
- **08. Data Export Reporting Engine** (`08-data-export-reporting-engine.md`)
- **09. Rate Limiting Throttling** (`09-rate-limiting-throttling.md`)
- **10. Idempotent API Design** (`10-idempotent-api-design.md`)
- **11. AI Decision Node Pattern** (`11-ai-decision-node-pattern.md`)
- **12. Immutable Audit Log Generator** (`12-immutable-audit-log-generator.md`)
- **13. Concurrent Constraint Demo Kit** (`13-concurrent-constraint-demo-kit.md`)
- **14. Offline First Conflict Resolution** (`14-offline-first-conflict-resolution.md`)
- **15. Background Job System** (`15-background-job-system.md`)
- **16. Notification Engine** (`16-notification-engine.md`)
- **17. Feature Flags** (`17-feature-flags.md`)
- **ODOO HACKATHON PLAYBOOKS** (`ODOO_HACKATHON_PLAYBOOKS.md`)

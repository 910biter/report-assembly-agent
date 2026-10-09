# Multi-user Access and Durable Queue Implementation Plan

> **For agentic workers:** Implement task by task with tests before production changes. Preserve existing uncommitted frontend pagination work. Do not commit or deploy without a separate request.

**Goal:** Administrator-created users have private resources and a reliable view of how many jobs are ahead of theirs.

**Architecture:** PostgreSQL owns identity, root-resource ownership and report queue state. Server-side authorization is centralized and applies to API routes and assistant tool reads. A single worker claims durable jobs and retains the existing cross-process execution lock. The UI presents login, account administration and an owner-safe queue position.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, Vue 3, TypeScript, pytest and Node test runner.

**Spec:** [multi-user-queue-design.md](./multi-user-queue-design.md)

**Current state:** Account/password/session primitives, owner columns, first-admin bootstrap, route-ID authorization, owner-scoped task/material/report lists and material hashes, graph workspace derivation, and PostgreSQL queue storage are implemented locally. The queue storage is not connected to the worker; template learning and fallback selection, assistant resource access, login hardening, UI, and cross-user end-to-end tests remain incomplete. `MULTIUSER_READY=False` intentionally prevents enabling this partial implementation. Do not deploy it as a multi-user feature.

## Global Constraints

- No public registration or embedded default password.
- Existing resources belong to the first administrator after migration.
- Material fingerprints, vector retrieval, graph workspaces and long-memory reads must be tenant-scoped before multi-user activation.
- Administrators do not bypass private-content authorization for resources owned by other users.
- Ordinary users see counts ahead, never other users' queue titles or IDs.
- Keep generation serial on the current GPU; do not run two queue implementations simultaneously.

## Review Focus

- Direct report export, version restore, assistant lookup or material ID guessed from another account must fail as 404.
- A disabled user with an existing session must lose access immediately.
- An enqueue transaction failure must not create an in-memory or database ghost job.
- A restart during a running job must not produce two executions or discard waiting jobs.
- Existing material hashes must not disclose another owner's material in deduplication responses.

## Task 1: Account schema, migration and bootstrap

**Files:** `app/infrastructure/orm.py`, `app/db.py`, new `app/auth/accounts.py`, new `app/auth/bootstrap.py`, `tests/test_auth_migration.py`.

**Interfaces:** `get_user_by_id(user_id)`, `create_user(username, password, role)`, `disable_user(user_id)`, `bootstrap_admin(username, password)`.

- [ ] Add failing tests for fresh schema, existing DB migration, unique usernames and historical resource assignment.
- [ ] Add `users`, `user_sessions`, ownership columns and idempotent backfill. Bootstrap must fail when no explicit administrator credentials are supplied.
- [ ] Verify no root resource is ownerless; existing data belongs to first admin and is linked through report/task/material/template chains.
- [ ] Run migration tests against PostgreSQL and inspect resulting constraints.

## Task 2: Login and request identity

**Files:** new `app/auth/sessions.py`, `app/api/auth_routes.py`, `app/api/__init__.py`, `tests/test_auth_sessions.py`.

**Interfaces:** `require_user(request) -> Principal`, `require_admin(request) -> Principal`, login/logout/me endpoints.

- [ ] Test bad credentials, revoked/disabled sessions, expired tokens, brute-force throttling and cookie flags.
- [ ] Implement hashed passwords and opaque database-backed sessions; do not use client-supplied user IDs.
- [ ] Enforce auth on all non-public API paths before route handlers, including future endpoints.
- [ ] Verify static login assets and readiness remain available without exposing private diagnostics.

## Task 3: Resource authorization and ownership propagation

**Files:** new `app/auth/ownership.py`, `app/api/routes.py`, `app/interaction.py`, `app/task_draft_agent.py`, `app/material_comparison.py`, `app/retrieval/store.py`, `app/graph/service.py`, memory/cache readers, `tests/test_cross_user_isolation.py`.

**Interfaces:** `require_task_owner`, `require_material_owner`, `require_report_owner`, `require_version_owner`, `require_thread_owner`, `require_template_owner`.

- [ ] Test cross-account list, detail, mutation, export, version, graph, assistant and comparison paths using guessed IDs.
- [ ] Set owner from the request principal at all creation points, including incremental and document-analysis tasks.
- [ ] Filter listings in SQL; check owner before accessing related records or starting background operations.
- [ ] Route assistant tool reads through the same ownership checks; ensure material hashes do not expose other owners' metadata.
- [ ] Remove global material fingerprint uniqueness in favor of owner-scoped uniqueness; scope Qdrant, graph and long-memory retrieval before candidate selection.
- [ ] Run existing report workflow tests and the cross-user suite.

## Task 4: Durable report queue

**Files:** new `app/workflow/queue_store.py`, `app/workflow/queue.py`, `app/workflow/execution.py`, `app/api/routes.py`, `tests/test_durable_queue.py`.

**Interfaces:** `enqueue(task_id, run_id, owner_user_id)`, `claim_next()`, `finish(entry_id, outcome)`, `queue_position(task_id, owner_user_id)`.

- [ ] Test FIFO across users, duplicate enqueue, pause/cancel, restart, stale running lease and per-user queue quota.
- [ ] Store each run in PostgreSQL with atomic task status transition and unique active enrollment.
- [ ] Claim with PostgreSQL row locking and retain the existing execution slot for one active report.
- [ ] Reconcile interrupted running rows using workflow checkpoints; waiting rows remain queued.
- [ ] Remove the old in-memory membership/order as a source of truth and compare queue positions against SQL rows.

## Task 5: UI login, admin and queue display

**Files:** `frontend/src/router.ts` or actual router entry, `frontend/src/pages/TasksPage.vue`, `frontend/src/pages/TaskWorkspacePage.vue`, new login/admin pages and shared auth client, frontend tests.

- [ ] Test auth redirect, account disable/logout, own list filtering and `N ahead` display without other users' content.
- [ ] Add login and a compact admin account-management view using existing shared UI primitives.
- [ ] Show queue position from server responses; avoid stale parse-progress labels in unrelated stages.
- [ ] Persist page-size preferences by authenticated user and clear private client caches on logout.
- [ ] Run typecheck, frontend tests and production build; verify desktop and mobile.

## Task 6: Integration and rollout

**Files:** integration tests, deployment instructions, migration script.

- [ ] Back up PostgreSQL and validate admin bootstrap before running ownership backfill.
- [ ] Exercise two users, one admin and three report jobs end to end, including restart while one job runs.
- [ ] Confirm report generation, versioning, graph, assistant and document parsing remain functional.
- [ ] Inspect logs for unauthorized reads, queue ghosts and duplicate runs. Only then deploy to the work service; do not touch the separate test service.

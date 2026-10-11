# Multi-user access and durable task queue

## Intent and boundaries

The application must support administrator-provisioned accounts without public registration. Each ordinary user owns private materials, tasks, reports, versions, templates, document analyses and conversations. Users can see their own queued jobs and the number of jobs ahead, but not other users' titles or content. Administrators manage accounts and inspect global queue/health, not private content by default. Report generation remains serial on the current single-GPU deployment.

The existing material-to-report workflow, report quality rules and version semantics do not change. This work changes identity, authorization and scheduling boundaries, not the analytical stages.

## Current state

- `app/api/routes.py` has direct task/report/material/version endpoints without a request identity or centralized authorization.
- `app/workflow/queue.py` has one in-process `PriorityQueue`; its membership and order disappear on restart. The persisted task payload contains only a status snapshot, not an authoritative queue row.
- `short_memory` is keyed by task ID and stores JSON; `reports.task_id` exists, but material reuse, versions, templates, comparisons, graph jobs and interaction threads use different roots.
- The client currently stores list page sizes in browser `localStorage`, not in user preferences.

## Identity and sessions

Create PostgreSQL `users` and `user_sessions` tables. Accounts have immutable IDs, unique normalized usernames, password hashes, role (`admin` or `user`), disabled state and timestamps. There is no self-registration route. A bootstrap command creates the first admin only when no admin exists; it must not embed a default password or silently recreate one.

Login issues an opaque, random session token in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie; store only a hash of the token in PostgreSQL. Production HTTPS is required for the secure cookie. Logout revokes the session. Password reset or account disable revokes all active sessions. Rate-limit login attempts and record account-administration actions. The server, never a client-supplied user ID, supplies the authenticated principal.

Unauthenticated requests can only access login, health readiness and static login assets. Health returns no private data. `/api/system/restart`, account administration and global queue diagnostics are admin-only.

## Ownership and authorization

Add `owner_user_id` to root records: tasks (`short_memory`), materials, style variants/templates and standalone interaction threads. Use task ownership for runs, artifacts, facts, inferences, graphs, comparisons and task-scoped interactions; use report ownership through `reports.task_id`, and version ownership through its report. Enforce these relationships on every read and write, including exports, bulk listings, assistant tool reads and background jobs. A foreign ID returns 404 to ordinary users.

The current global material fingerprint uniqueness must become owner-scoped. Equal files uploaded by two users get separate material records and per-user filenames/metadata; physical blob reuse is optional only behind an access-safe storage abstraction. Qdrant unit/material/fact retrieval must be constrained by the owning user's allowed material/task IDs before retrieval, not filtered only after a global top-K. The canonical graph and Neo4j projection must use per-user workspace keys; task membership alone does not isolate global entity resolution or assertion reuse. Global long-memory and cache reads must be audited and either scoped by owner or disabled for ordinary-user runs until safe. Template-learning jobs are currently process-local and ownerless; they must also carry owner identity, and every generated StyleVariant must inherit it. These are release gates, not optional later enhancements.

Authorization is a service/repository boundary shared by API endpoints and agent tools, not a frontend-only filter. Worker jobs carry an owner reference for audit but never trust it as authorization supplied by a request. Material deduplication may use a global file hash for storage efficiency, but a user cannot discover or reuse another user's material row without an explicit future sharing feature. No sharing is included in this scope.

Admins can list queue metadata and account status, not open user materials/reports by default. A future break-glass support flow would require a separate audited grant; no implicit admin bypass is added now.

## Existing data migration

Assign every existing task/material/template/conversation to the first administrator account during migration, preserving existing links. The administrator must be bootstrapped before ownership backfill; the migration must fail rather than guess if the administrator is missing or ambiguous. Newly created users cannot see these historical records. The migration must be idempotent and validate that no root record remains ownerless before authorization is enabled.

## Durable queue

Create a PostgreSQL `task_queue_entries` table with task ID, run ID, owner, priority, monotonic sequence, state, submitted/start/finish times, attempt count and lease/heartbeat metadata. A unique active-entry constraint prevents duplicate enrollment for the same run. Enqueue and task status changes occur in one transaction.

One worker claims the next eligible row in priority/sequence order with `FOR UPDATE SKIP LOCKED`, then acquires the existing cross-process execution slot. Queue position is computed from authoritative active rows, not `qsize()` or a sorted set. Running work is position zero; waiting users see a count ahead and their own enqueue time. Public responses contain no other users' names, task IDs or content. A queue-position estimate is not an ETA.

On process restart, queued rows stay queued. A running row with an expired lease is reconciled to paused/recoverable according to existing workflow checkpoints; it must never be executed twice. Pause/cancel and completion update both queue entry and task/run status atomically. Interactive assistant calls retain their separate model lane, but their resource policy must remain subordinate to the running report job. Per-user active-queue quotas prevent one user filling the queue; the initial scheduling policy is FIFO within equal priority, without hidden user priority.

## UI

Add a compact login screen and an admin-only user-management view for create, disable, reset password and session revocation. Normal lists show only owned resources. Task list/detail show `待运行 · 前方 N 个任务`; after activation they show the current stage rather than a stale material-parse counter. A global queue view for admins shows state and resource metrics, not private titles/content.

Persist list page-size preferences by authenticated user in PostgreSQL; browser storage may cache them but is not the authority. Logout clears cached private state and invalidates in-flight requests. Existing unauthenticated browser preferences can be imported into a user's preferences once, only with that user's explicit session.

## Acceptance tests

- Two users cannot list, read, edit, export, ask the assistant about, or access each other's resources by guessing IDs.
- Two users uploading identical bytes see only their own filename/material row, and vector/graph/memory retrieval never returns the other's content.
- Admin can create/disable/reset users and inspect queue health, but cannot read private report/material endpoints.
- Three jobs from two users retain FIFO order and correct `ahead` counts; pause/cancel/restart update counts without ghost entries.
- Restart while one job runs and another waits preserves the waiting job and never duplicates the running job.
- Existing tasks/materials/versions remain linked after migration and are owned by the first administrator; ordinary users cannot see them.
- Existing single-user report workflow and report-quality tests continue to pass.

## Rollout

Back up PostgreSQL first. Apply additive schema migration and legacy ownership assignment, verify ownership coverage, then enable authorization for all API and assistant surfaces. Switch queue writer/reader atomically to PostgreSQL; do not operate old and new schedulers together. Deploy login and filtered UI after server authorization is enforced. Run cross-user security and queue-restart tests before allowing additional users.

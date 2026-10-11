# Multi-user rollout

The multi-user code is available but remains disabled by default. Do not switch an existing work service without a PostgreSQL backup.

1. Back up PostgreSQL and deploy the application and matching `web/spa` build together. Start once with `IRA_MULTIUSER_ENABLED=false` so additive tables and owner columns are created.
2. In that same project environment, run `python -m app.auth.bootstrap ADMIN_NAME`. The command prompts for a password and atomically assigns existing tasks, materials, templates, conversations and reports to the first administrator. It fails if any account already exists.
3. If an administrator already exists, run `app.auth.accounts.backfill_existing_to_first_admin()` explicitly after checking that the first active administrator is the intended historical owner. `assert_multiuser_data_ready()` must pass before enabling login.
4. For a private-network HTTP test only, set `IRA_MULTIUSER_ENABLED=true` and `IRA_AUTH_HTTP_TEST_MODE=true`. Use `IRA_AUTH_ALLOWED_ORIGINS` only for an exact reverse-proxy browser origin. Never expose this mode publicly.
5. For production, terminate HTTPS at the application entry, set `IRA_MULTIUSER_ENABLED=true`, leave `IRA_AUTH_HTTP_TEST_MODE=false`, and configure any reverse-proxy origin explicitly. Login cookies are then `Secure`, `HttpOnly` and `SameSite=Lax`.

The administrator creates ordinary accounts in `/admin/users`. Materials, tasks, reports, versions, templates and assistant threads are private to each account. The global report queue remains serial; users see only their own jobs and a count of jobs ahead. An interrupted running job is paused at restart rather than executed twice, while queued jobs remain queued. The system refuses to start in multi-user mode when there is no active administrator or any root record lacks an owner.

The isolated regression run uses `IRA_TEST_DB_URL` and must never point to the work database. The HTTP test flag and test credentials must not be copied into the production `.env`.

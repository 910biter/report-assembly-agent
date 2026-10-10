# Model Connection

Administrators configure a single OpenAI-compatible Chat Completions endpoint in
System Settings. Workflow and collaboration share this connection. PostgreSQL
stores the settings; the API key is encrypted using a deployment-local key in
`runtime/secrets/model_connection.key`. Back up this key with the database. A
missing key must not be replaced to recover encrypted credentials.

The endpoint must include the API prefix (usually `/v1`). The service must support
Chat Completions, including function tools for the assistant. "OpenAI-compatible"
does not imply support for every provider or reasoning model. Test Connection
performs a small generation request, which may incur API charges; `/models` is
only a passive readiness probe. Generic mode never sends vLLM extension fields.
Select vLLM only for vLLM; enable priority only if its server uses that scheduler.
Choose the output parameter and disable temperature for services that reject it.

Capacity fields describe the actual serving limit, not requested report length.
Each stage retains its output target, capped by the configured output limit;
input packing uses the configured physical window. Tokenizer directories must
exist locally; absent one, the application explicitly estimates prompt tokens.
API usage remains authoritative for actual token consumption. Never interpret
request duration as measured prefill/decode time. Remote GPU telemetry is not
provided by the Chat Completions interface. Hardware samples describe the
application host only. Embedding, parsing and vector storage remain local.

Changing the connection is rejected during active tasks/model calls. Model-aware
artifact caches include endpoint/model/capability identity. A queued task uses the
connection configured when it begins; paused tasks may resume with a changed
connection, so review the recorded call identity when comparing runs.

Old `IRA_GENERATION_URL/MODEL/API_KEY/TOKENIZER_PATH` values are no longer read at
runtime. Existing deployments can explicitly migrate once with
`python -m deploy.migrate_model_connection`; fresh installations configure the UI.
This command does not start, stop or alter vLLM. Deploy model servers independently;
switching to a remote API must not start a local model server automatically.

Inner-network HTTP remains test-only: Cookie and Key encryption at rest do not
protect credentials in transit. Use HTTPS for production browser/API access.

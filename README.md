# Agentic Cloud Assistant

React dashboard and FastAPI/FastMCP backend for AWS inventory, security checks,
cost reporting, document retrieval, and reviewed infrastructure changes.
Implementation priorities and progress are tracked in [to_improve.md](to_improve.md).

## Development setup

Use Python 3.11 (matching the backend image) and Node 22. The frontend Docker
image uses Node 22 too. Run commands from the project root unless stated otherwise.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt -r backend/requirements-test.txt
python -m pip check
cd frontend
npm ci
```

`backend/requirements.in` lists direct runtime requirements. The generated
`backend/requirements.txt` pins the resolved dependency graph and is consumed
by Docker. `requirements-test.in` and `requirements-test.txt` define a smaller,
runtime-compatible environment for offline unit checks; install that file alone
when you only need the default tests. Locks target Python 3.11 on Linux.

To intentionally update dependencies, use uv 0.12.5 and review both lock diffs:

```bash
uv pip compile backend/requirements.in --python-version 3.11 --output-file backend/requirements.txt
uv pip compile backend/requirements-test.in --constraint backend/requirements.txt --python-version 3.11 --output-file backend/requirements-test.txt
```

Do not edit generated pins by hand. Full runtime installation includes the
embedding stack and PyTorch and may require substantial download/disk space.
Dependency resolution does not prove runtime compatibility; the lightweight
CI suite exercises HTTP/MCP startup with a stubbed knowledge base; the real
embedding stack and full runtime environment are not yet smoke-tested.

## Checks

```bash
cd backend
python -m pytest
python -m pip check
```

The default suite tests security rules, mocked Ollama interactions, SQLite
migration/recovery/concurrency, and real HTTP/MCP routing with a stubbed knowledge
base. It makes no AWS or model-service calls. RAG functional tests use the live knowledge-base
singleton and embedding model; the existing RAG HTTP tests target obsolete
routes. They are excluded from default discovery pending the planned RAG test
update. Do not run these suites against production data.

```bash
cd frontend
npm run lint
npm test
npm run build
```

GitHub Actions runs these offline tests and frontend checks on pushes and pull
requests without cloud credentials. No live infrastructure mutations are tested.

## Running the application

Copy `backend/.env.example` to `backend/.env` and configure server-side AWS and
model access. Never commit the resulting file. With the development environment
activated, run `uvicorn main:app --reload` from `backend`, and `npm run dev` from
`frontend`. For containers, use `docker compose up --build` from the project root.

Before starting HTTP mode, configure authentication as described below. There is
no default password and the former demo login no longer works.

`python main.py` and `python mcp_server.py` expose the same HTTP application.
`python mcp_server.py --stdio` retains the MCP-only local transport. Automatic
startup workdir deletion has been removed: remote-state executions may not have
a local state file, so absence of that file is insufficient evidence for deletion.

## Persistent storage and upgrades

Docker mounts three named volumes: `app_data` at `/app/data` for SQLite and
migration backups, `chroma_data` at `/app/chroma_db`, and `terraform_workdirs`
at `/app/terraform_workdirs`. Outside Docker, defaults are `backend/data`,
`backend/chroma_db`, and `backend/terraform_workdirs`. `ACA_DATA_DIR` and
`CHROMA_DB_PATH` configure the first two. SQLite uses schema version 3, including
server sessions, login throttling and verified scan identity records. Upgrading
a version-1 or version-2 database first creates a SQLite snapshot under
`data/backups/schema-v1-*.sqlite3` or `schema-v2-*.sqlite3`, preserving execution
history and sessions. Job, approval and audit schemas arrive with their features.
Existing execution dictionaries and history response shapes remain supported.
Updating an unknown execution now raises an error rather than silently succeeding.

**Before upgrading an existing deployment, stop its writers and export its data
before rebuilding or recreating the old backend container.** The former image
stored execution history at `/app/execution_log.json` and may have stored Chroma
at `/chroma_db`, outside the mounted volumes. Container replacement cannot recover
those files once the old container is removed. Use `docker compose cp` to export
those paths if present, plus back up existing named volumes. Retain that host
backup until migration and post-upgrade checks succeed. Do not use `down -v`.

For Docker legacy import, stage the exports into `app_data` under
`/app/data/legacy/execution_log.json` and `/app/data/legacy/chroma_db`, then set
`ACA_LEGACY_EXECUTION_LOG` and `ACA_LEGACY_CHROMA_PATH` to those paths in
`backend/.env` before starting the new backend. For example, after building the
new image, stage exports from a host directory named `migration-backup`:

```bash
docker compose run --rm --no-deps \
  -v "$PWD/migration-backup:/legacy:ro" --entrypoint sh backend \
  -c 'mkdir -p /app/data/legacy; cp -a /legacy/. /app/data/legacy/'
```

That host directory should contain `execution_log.json` and/or `chroma_db/`.
Keep all previous writers stopped during export, staging, and migration. For
local development the default legacy sources are `backend/execution_log.json`
and the project-root `chroma_db/`; they can also be overridden by the two legacy
environment variables. Source files are never deleted by migration.

On first database initialization, JSON history is copied to a hash-named backup
under `data/backups` and imported in one transaction, preserving execution IDs,
ordering, and payloads. The import marker prevents old history from overwriting
subsequent updates. Malformed records, duplicate IDs, or a corrupt database fail
loudly. Correct the legacy input and restart after a failed import; do not delete
the database to hide an error. Legacy history added after initialization is not
automatically imported; stage it **before the first startup**.

Before Chroma opens, a legacy collection is copied to `data/backups`, copied to
a staging directory, then renamed into its destination. An already populated
destination is authoritative and is never overwritten or merged. Choose the
intended source before startup if two different collections already exist.

For ongoing backups, stop writers and back up all three volumes together. To
restore, stop the stack and restore those volumes from a matching backup before
restarting. Retaining SQLite alone does not preserve vector documents, Terraform
state, private key files, or plans. Test restore in an isolated deployment.

## Health endpoints

- `GET /health`: process liveness; remains HTTP 200 when a local dependency fails.
- `GET /ready`: HTTP 200 only when SQLite can transact, Terraform is installed,
  workdir/plugin-cache storage is writable, and the knowledge base can be read
  with writable storage and authentication is configured. Failures return HTTP
  503 with per-dependency statuses. Health/readiness are the only public probes.

Readiness makes no AWS, Ollama, or hosted-LLM calls and does not load embeddings.
It does not verify cloud permissions, model availability, or Terraform provider
compatibility. Compose waits on `/ready` before starting the frontend. Nginx and
the Vite development proxy expose both health paths and the Ollama HTTP routes.

## Authentication and server credentials

Generate the operator password hash interactively in the activated Python
environment; the password is not passed through command-line arguments:

```bash
cd backend
python -m services.auth hash-password
```

Copy the resulting hash to `ACA_OPERATOR_PASSWORD_HASH` in `backend/.env` and set
`ACA_OPERATOR_USERNAME`. Passwords must contain 12–1024 characters; stored hashes
use a random salt and PBKDF2-HMAC-SHA256 with 600,000 iterations. There is no
plaintext password setting or credential fallback. Never commit this configuration.
For local HTTP development, set `ACA_COOKIE_SECURE=false`; the code defaults to
`true`. For deployment, use HTTPS, `ACA_COOKIE_SECURE=true`, and configure
`ACA_ALLOWED_ORIGINS` with exact browser origins, including scheme and port,
without paths, wildcards, or a trailing slash. Restart the backend after changes.

The browser and API use the same origin through Nginx or Vite's development
proxy. Both proxies forward `/auth/`. The MCP URL must also be same-origin; its
default is `/mcp/`. Cross-origin API URLs are intentionally rejected by the
browser client so credentials and CSRF headers cannot leak to another host.

Login creates a random session token in an HttpOnly, SameSite=Strict, host-only
cookie. HTTPS mode uses the `__Host-aca_session` cookie name and the Secure flag;
local HTTP uses `aca_session`. Only a SHA-256 digest of the session token is stored
in SQLite. Absolute expiry defaults to eight hours (`ACA_SESSION_SECONDS`, range
60–86400). Sessions survive backend restarts, expire server-side, rotate on login,
and are revoked on logout. Changing the configured password hash or username and
restarting invalidates existing browser sessions. To revoke all sessions manually,
stop the backend and delete rows from the `sessions` table, then restart; preserve
execution history and the database itself.

Endpoints:

- `POST /auth/login`: JSON `{ "username": "...", "password": "..." }`; requires
  an allowed Origin and `X-ACA-Client: browser`. It sets the cookie and returns
  username, expiry, and a per-session CSRF token, never the cookie token.
- `GET /auth/session`: restores authenticated session information; otherwise 401.
- `POST /auth/logout`: revokes the browser session and clears its cookie.
- `GET /auth/config`: authenticated, non-secret provider configuration flags and
  server credential-source information. Configured does not mean verified/online.
- `GET /auth/aws-identity?region=eu-west-1`: authenticated, fresh STS verification
  of the server credential set for the selected region. This returns `aws_identity`
  with account ID, region, principal ARN, partition and verification time. Missing
  or unverifiable credentials return 503, without returning SDK credential details.

Cookie-authenticated state-changing requests, including MCP POSTs and uploads,
must carry `X-CSRF-Token`. The token stays in frontend memory and is restored
through `/auth/session`. Supplied Origin headers must match the allowlist.
Successful and failed private responses use `Cache-Control: no-store`. Invalid
login messages do not distinguish unknown usernames from incorrect passwords;
validation responses omit rejected input so passwords are not echoed.

Login is limited transactionally to 10 attempts per source and 60 total per
five-minute window. Forwarded IP headers do not affect throttling. With the
provided Nginx proxy, sources share the proxy's backend address and therefore
share the source limit; this is a conservative default for the single operator.
The server returns HTTP 429 and `Retry-After: 300` when throttled.

For external HTTP MCP clients or automation, generate an independent token:

```bash
python -m services.auth generate-token
```

Save the raw token once in the client secret store and put only its printed
`ACA_MCP_TOKEN_SHA256` digest in `backend/.env`. Clients send
`Authorization: Bearer <raw-token>`. This grants full operator access to protected
HTTP endpoints, including MCP and REST wrappers. Bearer requests do not use
cookies or CSRF tokens. Rotate the configured digest and restart to revoke the
old token; browser logout does not revoke machine tokens. MCP stdio remains a
local-process interface governed by access to the backend environment/files.

All HTTP tools, resources, docs, uploads and private-key downloads require a
session or bearer token. Unconfigured HTTP authentication fails closed with 503;
invalid credentials return 401, and invalid CSRF/origin checks return 403.

AWS credentials and Groq/Anthropic keys now belong in server configuration or the
server credential chain; `OLLAMA_BASE_URL` selects the server's local inference
endpoint. The settings UI exposes configuration status, model choice and region,
not secret fields. On upgrade, the frontend removes `aca_auth` and
`agentic_cloud_api_keys`, migrating only supported model/region values to
`aca_preferences`. Server sessions and CSRF tokens are never stored in localStorage.

Password and CSRF choices are informed by the primary
[OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
and [CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
guidance. Authentication establishes the single operator's identity;
transactional approvals and Terraform isolation remain later plan steps.

## AWS account and region binding

Every AWS-facing MCP tool/resource verifies server credentials through STS before
using service clients. A frozen credential snapshot (including temporary session
tokens) is reused throughout that operation, including work delegated to a thread.
Credentials resolve through the server's Boto3 chain: environment, profiles,
roles and supported server credential sources. Browser/MCP arguments can no longer
override AWS keys. Optional `ACA_AWS_ACCOUNT_ID` pins the server to one account.
Explicit tool regions take precedence; otherwise the server uses `AWS_REGION`,
then `AWS_DEFAULT_REGION`, then `us-east-1`.

The browser verifies identity on login, region changes and explicit re-verification.
It displays account ID/region and clears old views when the identity scope changes.
AWS controls remain disabled until verification succeeds; history and document
retrieval stay available. Requests carry `expected_account_id` and the selected
region, preventing an unnoticed server credential switch between UI verification
and operation start. Verification proves the credential identity, not service
permissions or the success of an inventory/cost scan.

Tool responses include `aws_identity`. Inventory workflows also return a `scan_id`;
SQLite retains scan ID, verified identity and service statuses, not raw inventory
or credentials. Findings link to that scan. Plans and agent actions retain their
verified identity in execution records; history displays it. Apply/destroy and
agent approval re-verify and compare account, partition and region before mutation.
If the caller omits the region for a recorded action, its recorded region is used.
Rejecting a proposal remains available without AWS connectivity.

The direct security-group tool now requires a `scan_id` from a successful
security-group scan, and rejects an account/region mismatch. Existing records
without verified identity remain visible but are not automatically trusted for
apply/destroy. Retain their state and recover manually in the original account,
or prepare a new reviewed plan with any necessary resource imports. Do not edit
identity metadata to bypass a blocked action.

Terraform receives the verified access key, secret key, temporary token and region
in its subprocess environment. Stale profile/role/token selectors are removed;
shared credential/config-file fallback is disabled for that process. The default
AWS provider gets an `aca_identity_override.tf.json` guard containing its region
and `allowed_account_ids`, without credentials; `TF_VAR_region` is also set to the
selected region. The guard is checked again before apply/destroy, and generated S3
state configuration includes the allowed account. The S3 bucket's endpoint region
can still differ through server `TF_STATE_REGION`; it is not the resource region.

Supported plans use root resources and the standard HashiCorp `aws`, `random`,
`tls` and `local` providers. A pinned HCL parser rejects unknown providers,
AWS credential/profile overrides, aliases, role assumptions, custom endpoints,
skipped identity checks, modules, embedded state/cloud configuration and
provisioners before Terraform execution. Explicit AWS resource/provider regions
must match the selected region or use `var.region`. Submitted configurations that
need a broader provider model are intentionally unsupported for now.

Chat context caches are keyed by verified partition, account, region and principal;
mutation attempts invalidate every regional cache entry for the affected account,
including failed attempts that might have changed infrastructure. S3/IAM are
account-global, and Cost Explorer uses its standard `us-east-1` endpoint: the selected
region labels connection scope and does not filter account-wide spend.

Compatibility: AWS tools no longer accept `aws_access_key_id` or
`aws_secret_access_key`; REST wrappers reject unknown fields. Use the server
credential chain. `aws_connection_identity` provides the identity through MCP;
pass its account ID as `expected_account_id` to guard a subsequent request.
The public health/readiness probes still make no AWS calls.

These checks establish caller-account consistency for the supported workflow.
They do not sandbox Terraform, validate every resource's ownership/policy, or make
apply/destroy approvals transactional. Terraform still runs in the backend process
environment; isolation and a complete execution policy remain future steps. Frozen
temporary credentials can expire during long operations; those failures are not
automatically retried as mutations.

Implementation references: [Boto3 credential configuration](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html),
[STS caller identity](https://docs.aws.amazon.com/boto3/latest/reference/services/sts/client/get_caller_identity.html),
and [AWS Terraform provider configuration](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/html).

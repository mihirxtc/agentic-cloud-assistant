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
uv pip compile backend/requirements-runner.in --constraint backend/requirements-test.txt --python-version 3.11 --generate-hashes --output-file backend/requirements-runner.txt
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
base. It also tests filtered retrieval, replacement rollback, scoring and billing periods
without an embedding download. Optional pinned-Chroma acceptance tests skip when
Chroma is not installed. It makes no AWS or model-service calls. Legacy RAG functional tests use the live knowledge-base
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
`frontend`. For containers, complete the isolated-runner bootstrap below before using
`docker compose up --build` from the project root. The web API can serve scans
without a runner, but Terraform execution never falls back to the host.

Before starting HTTP mode, configure authentication as described below. There is
no default password and the former demo login no longer works.

`python main.py` and `python mcp_server.py` expose the same HTTP application.
`python mcp_server.py --stdio` retains the MCP-only local transport. Automatic
startup workdir deletion has been removed: remote-state executions may not have
a local state file, so absence of that file is insufficient evidence for deletion.

## Persistent storage and upgrades

Docker mounts `app_data` at `/app/data` and `chroma_data` at `/app/chroma_db`
in the non-root web API. Only the trusted runner mounts `terraform_workdirs` at
`/workspaces` and its separate `runner_control` ledger at `/control`. Each job
receives one workspace subpath. A private `runner_credentials` volume is mounted
read-only in the API/background worker/runner; it is absent from Terraform job containers. Outside Docker, API defaults
remain `backend/data` and `backend/chroma_db`; Terraform still requires the runner.
`ACA_DATA_DIR` and `CHROMA_DB_PATH` configure API storage. SQLite uses schema version 5, including
server sessions, login throttling, scan provenance, idempotency requests, target
locks and execution transition events. Upgrading a version-1, version-2 or
version-3 or version-4 database first creates a SQLite snapshot under
`data/backups/schema-vN-*.sqlite3`, preserving execution history and sessions.
Durable jobs, worker claims and progress are persisted in this database. See Background jobs below.
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

For ongoing backups, stop writers and back up all five named volumes together.
Runner ownership/dispatch metadata must be backed up consistently with state and
SQLite. Store the credential-volume backup as a secret. To
restore, stop the stack and restore those volumes from a matching backup before
restarting. Retaining SQLite alone does not preserve vector documents, Terraform
state, private key files, or plans. Test restore in an isolated deployment.

## Health endpoints

- `GET /health`: process liveness; remains HTTP 200 when a local dependency fails.
- `GET /ready`: API readiness only: SQLite, knowledge-base storage and configured
  authentication. It does not need Terraform or a running job container.
- Authenticated `GET /ready/terraform`: separately checks the private runner,
  immutable job image and internal job network. HTTP 503 means execution is
  unavailable; scans and other API operations can remain usable.
Only `/health` and `/ready` are public probes.

API readiness makes no network calls or embedding loads. Runner readiness checks
local engine/image/network metadata, without AWS, model or Terraform execution.
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
transactional approvals are described below; Terraform isolation remains planned.

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
`tls` providers (the runner rejects `local`). A pinned HCL parser rejects unknown providers,
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
They do not sandbox Terraform or validate every resource's ownership/policy.
Transactional approvals are enforced by the separate reviewed-action workflow below. Terraform now runs in isolated job containers with the restricted resource policy
below; container-engine and host trust remain part of the deployment boundary. Frozen
temporary credentials can expire during long operations; those failures are not
automatically retried as mutations.

Implementation references: [Boto3 credential configuration](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html),
[STS caller identity](https://docs.aws.amazon.com/boto3/latest/reference/services/sts/client/get_caller_identity.html),
and [AWS Terraform provider configuration](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/html).

## Scan coverage and assessment limits

Full scans, security assessments and findings resources return `status` as
`complete`, `partial`, or `failed`, plus `coverage.services`, `coverage.rules`,
`scan_id`, verified identity, and start/completion timestamps. `complete` means
that the implemented inspections completed within their stated scope, not that
all AWS security controls were audited. Individual service responses retain
`status: ok|partial|error` for compatibility and add `coverage` with the three
assessment states.

Successful observations and confirmed findings survive failures elsewhere.
`inventory_complete: false` means observed counts are lower bounds. Resource
detail failures carry operation/error codes and use `null` for unknown values,
including MFA, key-last-used dates, public access and subnet counts. Unknowns
must not be interpreted as private, disabled, empty or zero. SQLite retains
coverage metadata with scan provenance; it does not retain raw inventory.
The dashboard displays coverage/errors and hides its health score for incomplete
assessments. Agent remediation stops before generation/planning when coverage is
incomplete. Re-scan feedback does not claim an individual fix is confirmed solely
because the total finding count is zero.

S3 inspects paginated bucket inventory, bucket ACLs, policy public status, and
bucket/account public-access blocks. It checks both AllUsers and AuthenticatedUsers
grants. It does not enumerate object ACLs, access points or organization controls,
or test anonymous object reads. A finding identifies public configuration within
that scope, rather than asserting every object is readable. Account block checks
require `s3:GetAccountPublicAccessBlock`; bucket details require
`s3:GetBucketPublicAccessBlock`, `s3:GetBucketAcl`, and `s3:GetBucketPolicyStatus`.
Denied calls produce partial coverage rather than an assumed private bucket.
[AWS public-access block semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html)
explain why `BlockPublicPolicy` alone does not suppress an existing public policy.

Security-group results retain both port endpoints, protocol, IPv4/IPv6 CIDRs,
prefix-list IDs, and source-group references. World-open findings keep their
source CIDRs; SSH matches TCP and RDP matches TCP/UDP ranges or all-protocol
rules. ICMP types/codes are not TCP/UDP ports. ENI usage scans describe only
observed ENI associations; no association does not prove deletion is safe.
See [AWS security-group rule semantics](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html).
IAM console inactivity uses an observed old login or a never-used user created
more than 90 days ago; it does not establish inactivity of programmatic access.


## Reviewed actions, retries and destroy plans

All exposed mutation tools now use a shared transactional executor. New actions
move through `planning` → `awaiting_approval` → `applying` → `complete`, `failed`,
`uncertain`, or `invalidated`; awaiting proposals can instead become `rejected`.
Planning failures use `plan_failed`. SQLite atomically claims an awaiting action
before dispatch and records transition time, authenticated operator/transport and
approval digest. In-process/stdio calls are labelled `local-process`/`stdio`.
History exposes transition events and permits resuming pending reviews in the UI.

The approval digest binds action type, account/region identity, review output,
and either the saved Terraform plan/configuration manifest or exact public
security-group permissions. Missing or changed artifacts invalidate the action
before dispatch. Runner manifests bind fixed root HCL/JSON artifacts, the required provider
lockfile, saved plan, job image ID, resource policy and ownership-ledger digest.
Additional configuration/variable files are rejected. Reviewed plan/provider artifacts are retained;
manage their storage as sensitive execution evidence. The runner rechecks those artifacts and structured plan policy inside the job
before dispatch. The trusted runner/engine/host remain outside the job sandbox.

`run_terraform_apply_mcp` and `agent_approve` require `approval_digest` from the
proposal and an `idempotency_key` for approval. The key must contain 8–128 ASCII
letters/digits/hyphens/underscores. Repeat the **same key and request contents** to
retrieve the recorded action without dispatching it again. Reusing a key for
other content, approving an already-claimed action with a new key, or approving
without the digest fails. Proposal tools also accept optional idempotency keys.
Rejection remains available offline, including for legacy pending proposals;
legacy proposals without a digest cannot execute and require a new proposal.

Compatibility changes:

- `revoke_open_ingress_rule` now **prepares a proposal**. Inspect `plan_output`
  for exact protocol, range and public IPv4/IPv6 sources, then approve its
  `execution_id`/digest via the shared approval tool. It does not revoke on proposal.
  Private CIDRs are excluded, and changed matching public rules require a new review.
- `rollback_execution` now **prepares a separate destroy proposal** for a completed
  reviewed Terraform apply. It does not destroy on proposal. Review and approve
  the returned destroy execution independently. Destruction uses `terraform plan
  -destroy -out=...`, followed only after approval by `terraform apply <saved-plan>`;
  the original workspace and state backend are retained. This is destruction,
  not restoration of the previous infrastructure configuration.
- Failed/uncertain actions cannot automatically execute again. A repeated request
  returns their current state; a new request key cannot bypass that state.

A target lock serializes pending Terraform actions sharing a workspace and active
SG mutations targeting the same account/region/group. Failed and uncertain
mutations keep their locks because remote changes may be partial. A process crash
can leave `planning`/`applying` persisted and locked; this never authorizes retry.
Check history and actual remote state. Do not clear database locks to force another
mutation. Expired worker claims are recovered without replay; see Background jobs below.
The UI labels uncertain results and no longer reports an immediate rollback.

Saved-plan semantics follow [HashiCorp's plan reference](https://developer.hashicorp.com/terraform/cli/commands/plan)
and [apply reference](https://developer.hashicorp.com/terraform/cli/commands/apply).
No live infrastructure operation is part of the automated offline test suite.


## Isolated Terraform runner

Step 7 removes Terraform and provider execution from the web image entirely.
The API calls an authenticated fixed-operation runner; no request can choose a
command, image, mount or privilege. Only the trusted runner has container-engine
access. Prefer an operator-managed rootless engine; the application does not
install/reconfigure a daemon. Engine access makes the runner trusted infrastructure,
not an untrusted job. Do not mount its engine socket in the web API or jobs.

Each job uses a locally built immutable image ID, UID/GID 10001, a read-only root,
one writable workspace subpath, scratch tmpfs, no added capabilities, no new
privileges, and CPU/memory/PID/output/deadline bounds. Jobs join an internal Docker
network; direct external routing/DNS is disabled. A CONNECT-only proxy permits
public AWS HTTPS endpoints and refuses private/reserved address routing. Providers
come from the image's read-only filesystem mirror, with no direct registry fallback.
PrivateLink/custom endpoints and arbitrary non-AWS network destinations are outside
this policy. Containers do not protect against a compromised host/engine/kernel.

Frozen credentials arrive over stdin and are reverified through STS inside the job.
They are not stored in container environment configuration, broker metadata or job
request files. Child processes receive an explicit allowlist, excluding model/login
secrets, profiles, arbitrary TF_VAR/TF_CLI_ARGS and library injection settings.
State/plans/private key artifacts themselves remain sensitive and need protected
volume backups. No secure-memory-erasure guarantee is made for trusted processes.

### Initial runner setup

These are **operator commands**, not actions performed during implementation:

```bash
cp backend/runner/config.example.json backend/runner/config.json
# Review resource_policy and optional state_backend in that file first.
docker compose --profile build build terraform-job
docker compose build terraform-runner
docker compose --profile setup run --rm runner-token-init
export ACA_JOB_IMAGE="$(docker image inspect aca-terraform-job:step7 --format '{{.Id}}')"
# Set these for the deployment-managed engine; do not change host permissions blindly.
# export ACA_DOCKER_SOCKET=/run/user/<uid>/docker.sock
# export ACA_DOCKER_GID=<socket-group-as-seen-in-runner>
docker compose up --build
```

Initialize the token volume **before** first startup so its UID 10001 ownership
comes from the bootstrap image. The token is created exclusively with mode 0400;
repeating initialization retains it without printing it. Rotate only after stopping
API/runner clients, using the explicit `runner-token-init --rotate` command. Back up
and provision credentials separately for a runner on another host; use HTTPS there.
Set `ACA_JOB_IMAGE` again after rebuilding; an image/policy change invalidates old
runner proposals and requires reviewed recovery/replanning, not a new digest for
old artifacts. Existing `.env` files and AWS IAM resources are never edited here.

[Docker Engine 26+](https://docs.docker.com/engine/release-notes/26.0/) is required for `volume-subpath` mounts; this is checked before
every job. The runner copies the pinned 29.9.3 CLI from the official Docker image
instead of relying on a potentially older distribution package. Use
`python -m runner.smoke --image <sha256-image-id>` from `backend` in the lightweight
Python environment to test the dummy non-root job. It creates/deletes only random
smoke-test volumes/networks, makes no AWS calls and supplies no AWS credentials.
The GitHub Actions isolation job builds the checked image and runs this smoke test.
Local Docker runtime/build verification was blocked by socket permissions in this
session. The published Step 7 images and dummy isolation smoke check subsequently passed in [GitHub Actions](https://github.com/mihirxtc/agentic-cloud-assistant/actions/runs/41772479505). Local deployment and live AWS execution remain unverified.

For a host-based API, use the optional loopback override:
`docker compose -f docker-compose.yml -f docker-compose.runner-dev.yml up terraform-runner terraform-egress`.
Set `ACA_RUNNER_URL=http://localhost:8090` and `ACA_RUNNER_TOKEN_FILE` to a private
local copy of the same token. Export it only to an ignored mode-0600 file, never
chat/logs or a shell argument. The Compose stack itself uses the private credential
volume and does not need a host token file. A missing/unreachable runner refuses
execution; installing host Terraform does not enable a fallback.

### Supported resource policy

Terraform 1.17.1; providers `aws` 5.100.0, `random` 3.7.2 and `tls` 4.1.0 are pinned
in `backend/runner/versions.json`, including published Linux amd64/arm64 archive
SHA-256 values. The installer verifies archives before extraction. The build creates
a canonical lockfile from that checked mirror. Runtime initialization uses
`-lockfile=readonly`; checksum mismatches fail instead of deleting locks/caches.
Plans remain tied to the same image and architecture. See [HashiCorp releases](https://releases.hashicorp.com/terraform/1.17.1/)
and [readonly lockfile behavior](https://developer.hashicorp.com/terraform/cli/commands/init).

The first supported policy deliberately limits configuration to:

- Private S3 buckets with `force_destroy=false`, owned public-access-block,
  enabled versioning and AES256 encryption companions.
- Bounded RFC1918 VPCs/private subnets, explicit TCP/CIDR security groups, small
  private EC2 instances, AWS key pairs, random suffixes and one RSA-4096 SSH key.
- EC2 requires an AMI in `resource_policy.allowed_amis`, IMDSv2, encrypted 8–30 GiB
  root storage and an approved nano/micro/small t3/t4g type. The default AMI list
  is empty, so EC2 proposals are denied until explicitly configured.
- Existing target IDs must appear in the server's `authorized_targets` mapping
  for their resource type, or be direct references to resources owned by this
  workspace. Inventory alone grants no authorization. SSH/RDP ingress requires
  `admin_cidrs`; world-open rules are limited to individual TCP ports 80/443.

IAM, RDS, modules, imports, data sources, local-file providers, provisioners,
user-data programs, filesystem-reading functions, aliases and unsupported arguments
are rejected before initialization. The structured saved plan is checked before
approval and again before apply. Critical unknowns fail closed. Updates/deletes
require matching resource IDs in the runner ownership ledger, and ownership tags
where supported. Replacements are outside this first policy. The runner exports
SSH material only to `ssh-key.pem`; that is the only downloadable key filename.
Static HCL validation no longer claims provider validation—it explicitly requires
an isolated plan before approval.

### Existing data and state backend

Stop writers and export/back up existing data before upgrading. New services run
as UID/GID 10001; existing root-owned volumes need a deliberate, backed-up ownership
migration for that UID. Use a narrowly scoped operator-owned migration container,
not a running API/job or a recursive host-directory ownership change. Do not remove
volumes to solve permissions. The workspace volume defaults to the original project
name `agentic-cloud-assistant_terraform_workdirs`; set `ACA_WORKSPACE_VOLUME` to the
actual old volume name if the original Compose project name differed. The API no
longer mounts it. Back up the new runner control ledger with every state snapshot.

Legacy state/plans/keys remain in their volume, but are not automatically adopted
into the runner ledger. Legacy pending approvals cannot execute; legacy destroys
and key downloads require manual recovery from a stopped backup or a separately
validated migration. Do not fabricate ownership or dispatch evidence. Read-only
history and AWS scanning remain available.

To use remote state, set the same existing bucket/region in backend `TF_STATE_*`
and runner `state_backend`. Native `use_lockfile=true` is supported by the pinned
engine. IAM needs ListBucket for permitted workspace prefixes, GetObject/PutObject
on `<workspace>/terraform.tfstate`, and GetObject/PutObject/DeleteObject on the
matching `.tflock` object. A DynamoDB lock table is not used. Version/encrypt the
bucket through your explicit infrastructure process. The old setup helper now
prints these instructions and performs no AWS calls or private-config writes.
[HashiCorp S3 locking and IAM reference](https://developer.hashicorp.com/terraform/language/backend/s3).

Dispatch evidence is persisted by both API and runner before mutation. Failed,
uncertain or interrupted executions stay blocked; one-shot plans cannot be replayed
after a lost response. Durable job scheduling and reviewed reconciliation are
handled conservatively by the background job workflow below. Do not clear locks/ledger entries to force execution.


## Background jobs, progress and recovery (Step 8)

Long AWS scans, security/cost assessments, Terraform generation/planning, agent
runs, ingress proposal preparation and destroy planning now return a persisted job
instead of keeping the original request open. Approving a change atomically claims
the reviewed action and queues its execution. Rejection stays local. Existing tool
names and arguments remain, but clients must follow the returned `job_id` to its
result. Bundled browser clients do this automatically. Chat, RAG, local validation,
identity checks and short history/status operations retain their current interfaces.

**Start the worker as well as the API.** Compose includes `job-worker`, built from
the backend image with the same server configuration, data/Chroma volumes and private
runner API credential. It has no Terraform binary, workspace mount or engine socket.
For host development, use another terminal with the same virtual environment and
server configuration:

```bash
cd backend
python -m services.job_worker
```

Run the worker from `backend` just like the API. It loads `backend/.env` through the
shared tool registry. It resolves fresh server credentials and verifies the saved
AWS account/partition/region before executing each job. Job inputs never store the
server AWS credentials, model API keys or session tokens. Nonempty caller `api_key`
arguments are rejected for queued tools; configure models on the server instead.
Treat requests/HCL and saved results as sensitive application data. Unlike the scan
provenance table, completed job results can contain raw inventory and model output.
There is currently no automated job-result retention cleanup; include the database
in protected backups and size monitoring.

Authenticated interfaces (browser session/CSRF or configured bearer token):

- `GET /jobs?limit=50`: recent job summaries, bounded to at most 100.
- `GET /jobs/{job_id}`: stage events, timestamps, identity, linked execution and result.
- `POST /jobs/{job_id}/cancel`: cancel only while queued. If this is an approved
  action, cancellation and rejection/lock release commit together before any worker
  can claim it. A running mutation cannot be cancelled or undone through this route.
- MCP tools `list_jobs`, `get_job`, `cancel_job` expose the same lifecycle.
- `GET /ready/jobs`: authenticated worker heartbeat readiness, separate from API
  and Terraform runner readiness. With no worker, requests remain queued.

An explicit proposal `idempotency_key` also deduplicates its submission; changed
content under the same key is rejected. Approval request keys deduplicate the
approval/job transaction. Unkeyed read requests are distinct jobs. On a network
failure, inspect Background Jobs before submitting another request. The browser
polls saved stages; it never resubmits a mutation because polling failed. Reopening
the dashboard lists recent jobs and lets the operator reopen their results and
pending action reviews. Completed proposal views reflect the action's current
status, so an old job does not present a completed change as awaiting approval.

Workers use atomic claims, 10-second heartbeats and 60-second leases. A subsequent
worker identifies expired work; it never requeues it automatically. A durable
already-saved action result is restored when available. Otherwise read/planning
jobs become `interrupted`; mutations become `reconciliation_required`, with the
action marked `uncertain` and its lock retained. A stale worker cannot overwrite
that recovered job. Running reads/plans are not forcibly cancelled; create a new
request after inspecting the old operation. No claim provides exactly-once
execution of an external AWS API across arbitrary process/network failure.

Use `inspect_interrupted_action` to read runner outcome evidence or current SG
permissions without mutating infrastructure. The runner now durably records apply
dispatch and terminal results in its control ledger. A successful terminal outcome
can be reviewed and confirmed with `reconcile_action(execution_id,
evidence_digest, ...)`; the evidence is checked again and an active job cannot be
unlocked. Confirmation restores completion and releases its target lock without
replaying Terraform. These controls are available in the Background Jobs detail.

An absent result, busy/unreachable runner, partial/failed apply, legacy dispatch
without terminal evidence, or interrupted direct SG call remains unresolved and
locked. Current SG permissions alone cannot prove an older SDK call has stopped.
Stop and inspect the original worker/runner, preserve database/control/state
backups, and validate AWS state before a separately reviewed offline recovery.
There is deliberately no general-purpose force-unlock or automatic retry endpoint.
An approved execution finishing successfully still requires a rescan to establish
whether the original finding was fixed.

Before this schema upgrade, stop **all API and worker writers**, back up application
and runner/state volumes, and upgrade them together. Schema 4 gets a backup before
migration; legacy in-flight actions become review-only job records and are never
adopted for execution. Restarted queued jobs run only through the configured worker.
Compose grants the worker ten minutes for graceful shutdown; forced termination
uses the interrupted-job recovery behavior above. Local process/container restart
and live AWS acceptance still need deployment-level validation.


## Retrieval, check scores and billing periods (Step 9)

Knowledge queries apply the resource predicate inside Chroma **before** selecting
nearest neighbours. New metadata stores canonical lowercase resource types; reads
also accept the legacy UI labels such as `S3`, `IAM` and `Terraform`. Source order
follows retrieval order. The relevance threshold remains 0.3, with full-precision
cosine distance used for thresholding rather than rounded display scores.

Replacing a document with the same ID removes stale tail chunks and old metadata.
Embeddings are prepared before any vector-store change. Every application read,
write, delete and count shares a filesystem lock; a durable private
`.aca-replacement.json` snapshot journal is written before vector changes. A failed
or interrupted update restores the previous document before the next application
read. If restoration fails, accesses fail instead of exposing a mixed revision.
Blank replacement text is rejected; use explicit deletion. Deletion matches the
exact `doc_id` metadata rather than matching other documents by ID prefix.

This guarantee applies through `SecurityKnowledgeBase`, including API/worker
processes sharing the same local storage and lock. Direct collection access or an
older application version bypasses that boundary. Stop all writers before upgrading;
keep the lock/journal files with the Chroma backup and do not manually remove an
outstanding journal to force access. A response lost after a successful update may
require the operator to inspect/retry the document; this is not cross-store
transactional infrastructure execution. Pinned Chroma 1.5.7 acceptance tests cover
its actual API, restart rollback and visibility between processes.

The default offline suite includes deterministic retrieval fixtures. A separate
isolated evaluation uses a temporary database and never opens the operator's KB:

```bash
cd backend
# Requires the project's pinned chromadb dependency; no embedding download.
python -m rag.evaluate --output /tmp/aca-retrieval-fixture.json
# Real embedding evaluation with an existing local SentenceTransformer model:
python -m rag.evaluate --embedding-model /path/to/local/model --output /tmp/aca-retrieval-semantic.json
# Optional alternate CPU ONNX runtime for the same MiniLM model family.
# Explicitly downloads its checksum-verified model to this temporary cache if absent:
python -m rag.evaluate --onnx-cache /tmp/aca-minilm-onnx --output /tmp/aca-retrieval-onnx.json
# Actual vector-store acceptance without embedding downloads:
python -m pytest tests/test_knowledge.py
```

The checked-in corpus has five positive and two negative queries, including a
wrong-resource distractor. Local fixture and MiniLM ONNX runs both produced
hit@1/recall@3/MRR 1.0, negative accuracy 1.0 and zero filter violations. This is a
small regression baseline, not a broad benchmark, answer-grounding evaluation,
or proof that the production SentenceTransformer runtime performs identically.
Expand the corpus using representative operator queries before tuning thresholds.

Security assessments now return `health_score`, with the method
`weighted_applicable_rule_families_v1`. Each applicable, fully inspected built-in
rule family has one weight (SSH/RDP/S3/all-traffic 3 each; MFA 2; inactivity/default
VPC 1 each). A family fails if it has any confirmed finding; repeated affected
resources do not subtract the same weight repeatedly. Families without observed
resources are excluded from the denominator. Incomplete coverage, unknown checks,
unknown finding rules and an entirely empty scope produce `value: null`.
The dashboard uses the backend result and labels it **CHECKS**, with its limited
scope explained in the tooltip. This percentage does not rate account-wide security.
Existing saved assessments lacking this method do not receive a fabricated score;
run a new assessment to obtain one.

Billing reports separate **month to date** from **completed calendar months**.
UTC dates define the window; the end date is exclusive. On the first day there
are no completed days in the new month, so current-month cost is unavailable and
no future-ended API request is made. The percentage/anomaly compares the two latest
contiguous completed calendar months in the same currency. The frontend uses that
backend comparison, never a partial month divided by a full month.
`estimate_costs.time_period_days` remains a compatibility input for an approximate
calendar history horizon (rounded up, at least two and at most twelve months),
not an exact rolling-day invoice window.

Every Cost Explorer page must succeed before publishing an aggregate. Missing
results, invalid/incomplete months, incompatible currencies and API errors remain
unknown; an explicit successfully returned zero remains zero. Zero/negative
baselines do not yield percentage claims. Values preserve currency and estimated
flags; service credits remain visible. `comparison` carries periods and the signed
percentage; `anomaly` retains the same object for compatibility. Coverage/errors
travel with tools, chat context and the cost resource. Model failure retains the
report, and incomplete billing data is not sent to a model for a fabricated summary.

API semantics: [Chroma collection query/filter interface](https://github.com/chroma-core/chroma/blob/main/chromadb/api/models/Collection.py)
and [AWS GetCostAndUsage](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html).
No live billing request or cloud mutation was made during these tests.

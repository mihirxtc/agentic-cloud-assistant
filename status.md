# Agentic Cloud Assistant — project status

Updated: **4 November 2026** (Europe/London).

## Current position

**Improvement steps 1–7 are implemented. Step 8 is next and has not started.**
Steps 5–6 completed locally on 3 November; Step 7 completed on 4 November 2026.
Steps 5–7 and this checkpoint are uncommitted and have not been pushed.
**Step 7 container-runtime acceptance is pending:** Docker socket permissions
prevented local image builds and smoke tests; the configured CI check has not run.

Steps 1–4, tests, setup documentation, and progress files were committed as
[`21813ae`](https://github.com/mihirxtc/agentic-cloud-assistant/commit/21813ae4c6153de921c89cb1b6390aed911e3ada)
and successfully pushed to GitHub `main` on 3 November 2026. The remote branch
was verified to point to that commit. It has **not been deployed**.
No live AWS resources were changed, no live account identity was verified, and no
real operator password, bearer token, private `.env` file, or user database was
provisioned or migrated during this work.

The application remains a single-operator AWS management prototype. Authentication,
account binding and transactional approvals are implemented. Terraform now has an
isolated-runner implementation and a constrained resource policy, with container
runtime verification pending. Durable background jobs are not yet implemented.

## Completed work

### Step 1 — reproducible setup and automated checks

- Added backend dependency input files and generated Python 3.11 runtime/test locks.
- Added an offline pytest configuration and GitHub Actions checks.
- Aligned frontend Docker/CI on Node 22 and retained its existing npm lockfile.
- Fixed baseline frontend lint problems and an outdated Ollama test fixture.
- Added setup and validation instructions in [README.md](README.md).

### Step 2 — shared startup and durable storage

- Both HTTP entrypoints use [backend/http_app.py](backend/http_app.py).
- MCP stdio remains available as a separate local-process transport.
- Execution history now uses transactional SQLite instead of rewriting JSON.
- Legacy history import creates a byte-identical backup before parsing/importing;
  invalid imports roll back and errors remain visible.
- Legacy Chroma migration backs up and stages a copy without overwriting a populated
  destination or deleting the source.
- Compose persists application data, Chroma, and Terraform workdirs in named volumes.
- `/health` reports liveness; `/ready` checks local dependencies and authentication
  configuration without making AWS or LLM calls.
- Removed automatic startup workdir purging because remote state may exist without
  a local state file.

### Step 3 — backend authentication and browser secret removal

- Replaced hardcoded browser login with a server-configured operator and salted
  PBKDF2-SHA256 password hash; there is no default password.
- Added persistent, expiring sessions, hashed session tokens, logout revocation,
  login rotation, and credential-change invalidation.
- Added HttpOnly/SameSite cookies, HTTPS Secure cookies, CSRF validation, exact
  origin checks, and transactional login throttling.
- Protected HTTP MCP, REST wrappers, documentation, uploads, and key downloads.
- Added optional bearer authentication for external clients; stdio retains its
  local process/filesystem boundary.
- Removed browser secret fields and outgoing cloud/model keys. Legacy stored keys
  and the old login flag are purged; only model/region preferences persist.
- Added same-origin authenticated fetch, session restoration, secret-safe validation
  errors, non-secret configuration status, and private-response cache prevention.

### Step 4 — verified AWS account and region binding

- Added [backend/services/aws_connection.py](backend/services/aws_connection.py):
  resolve server credentials, freeze them, and verify that exact set through STS.
- SDK clients and Terraform lifecycle operations share the verified snapshot,
  including temporary session tokens. Optional `ACA_AWS_ACCOUNT_ID` pins an account.
- Removed caller-supplied AWS credential arguments; REST wrappers reject unknown
  fields instead of silently ignoring them.
- Persisted verified identity with plans/agent actions and scan provenance.
- Apply, agent approval, and destroy compare account, partition, and region before
  mutation. Legacy records without identity remain readable/rejectable but are
  blocked from automatic apply/destroy.
- Direct security-group fixes require a recorded successful SG scan and matching
  account/region. Findings carry scan IDs.
- Chat caching is scoped by partition/account/region/principal; mutation attempts
  invalidate the affected account's regional entries.
- Terraform receives a default-provider account/region guard. Pinned HCL parsing
  rejects credential overrides, unguarded aliases, unsupported providers/modules,
  embedded state configuration, provisioners, and conflicting regions.
- UI verification displays account/region, clears stale views, and prevents
  unverified AWS actions. History and knowledge-base access remain available.

At Step 4 these were account/region configuration guards rather than isolation.
Step 7 now adds a runner boundary and narrows active execution to explicit root
resources and pinned aws/random/tls providers; local-file providers are rejected.

### Step 5 — explicit scan coverage and unknown results

- Full scans and assessments share complete/partial/failed coverage, per-service
  errors/timestamps, per-rule unknown-resource IDs, and scan/account provenance.
- Partial pagination preserves observed inventory; partial assessments retain
  confirmed findings. SQLite retains coverage metadata without raw inventory.
- S3 inspects paginated buckets, bucket ACL/policy public grants and effective
  bucket/account public-access blocks; unknown permissions remain unknown.
  Object ACLs, access points, organization controls and anonymous reads are outside
  the stated inspection scope.
- IAM details paginate and preserve unknown MFA/key activity; VPC subnet failures
  remain unknown. Console inactivity does not claim programmatic inactivity.
- SG ingress preserves ranges, protocols, IPv4/IPv6 CIDRs and source references.
  Findings and direct fixes agree on TCP SSH/TCP-UDP RDP/all-protocol matching;
  ICMP types/codes are excluded. Failed ENI inspection cannot label SGs unused.
- Dashboard shows coverage/errors, unknown inventory and partial findings; incomplete
  health scores are hidden. Agent planning stops on incomplete coverage. Rescans
  do not claim an individual fix is confirmed from a global zero-finding count.
- Model-summary failures retain findings and deterministic incomplete/no-finding
  wording avoids broad security claims.

### Step 6 — review-bound transactional execution

- Schema 4 stores idempotency requests, target locks and actor/time transition events.
  Approvals claim an awaiting action atomically; conflicting approvals/rejections
  cannot both dispatch. Legacy proposals remain rejectable but cannot execute.
- Approval digests bind action/account/region/review content to saved plan/config
  hashes or exact SG permissions. Changed/missing artifacts and SG drift invalidate
  before dispatch. Plan/provider artifacts are retained as sensitive evidence.
- Same-key/request retries return persisted status/result; reused keys with changed
  contents fail. Failed, uncertain and crash-left actions cannot silently retry.
- Direct ingress requests now prepare reviewable proposals. Manual and agent
  approvals share the executor and revoke only reviewed public permissions.
- Rollback requests now prepare separately approved saved destroy plans in the
  original workspace/state; no public path immediately invokes terraform destroy.
- React shows account/region/digest and exact planned changes, sends action-scoped
  decision keys, and distinguishes uncertain/in-progress results. History can resume
  pending reviews and exposes transition events with REST/MCP/stdio attribution.
- Shared-target locks remain held after failed/uncertain mutation. Recovery and
  reconciliation are deliberately deferred to Step 8; there is no automatic unlock.

### Step 7 — isolated runner and explicit execution policy

- Non-root web API has no Terraform executable, provider subprocesses, engine
  socket or workspace mount. Static HCL checks do not launch Terraform. A private
  fixed-operation runner handles plan/apply/destroy-plan/artifact operations.
- Trusted runner alone accesses the engine. Per-job non-root containers receive
  one workspace subpath, read-only root/tmpfs, capability/resource/runtime limits,
  internal networking and an AWS-HTTPS egress proxy. These settings await real
  container validation; dummy-input CI smoke checks are configured.
- Credentials arrive over stdin, are reverified by STS inside the job, and reach
  Terraform through an explicit environment allowlist. Application secrets and
  arbitrary CLI/profile/TF_VAR settings are excluded.
- Source and structured-plan policy constrains resource types, approved AMIs/targets,
  network/storage settings, ownership IDs/tags and critical unknowns. Private SSH
  material is restricted to a fixed export; path and file access is bounded/no-follow.
- Saved-plan approval now binds image/policy/ownership evidence as well as artifact
  hashes. Legacy workspaces are preserved but not adopted. Runner dispatch evidence
  is persisted before launch; lost responses cannot replay a saved plan.
- Terraform 1.17.1 and aws 5.100.0/random 3.7.2/tls 4.1.0 archives are checksum-pinned;
  runtime provider locks are canonical/read-only. Docker CLI 29.9.3 is pinned and
  Engine 26+ is checked before each job. No cache-deletion/checksum retry remains.
- Added private token-volume bootstrap, separate API/runner readiness, loopback-only
  dev access, native S3 lock instructions, Compose migration/ownership docs and
  CI image/isolation checks. No real token, image, deployment or migration was made.

## Current architecture and storage

- React/Vite → authenticated FastAPI/FastMCP → AWS/model/RAG services; Terraform
  goes through the private runner into isolated job containers.
- Docker/local HTTP startup shares one app factory; stdio shares the tool registry.
- SQLite is currently **schema version 4**, holding execution payloads, sessions,
  login throttling, scan coverage/provenance, idempotency requests, target locks and events.
- Upgrades from schemas 1/2/3 create SQLite backups before migration. Scan records
  retain provenance, not raw inventory or credentials.
- API volumes: app_data/chroma_data. Runner volumes: terraform_workdirs at
  /workspaces and runner_control at /control. Jobs mount one subpath at /workspace.
  The runner_credentials volume is read-only in API/runner and absent from jobs.
- API local defaults remain backend/data and backend/chroma_db; Terraform requires
  a configured runner even for local development. See README for volume names and
  UID-10001 ownership migration; do not delete/replace existing volumes.

## Latest verification results

Last implementation verification, recorded on 4 November 2026:

- **273 backend tests passed**: rules, mocked providers, authentication, storage,
  migrations, HTTP/MCP routing, AWS identity, cache separation, guarded execution,
  runner contracts, resource/path policy, token handling and job integrity.
- **23 frontend cases passed** across session, preference, AWS identity, coverage and approval tests.
- Frontend lint and production build passed.
- The unchanged dependency environment last passed its **94-package** `uv pip check` on 29 October.
- Python compilation, shell syntax, both Compose configurations and whitespace checks passed.
- Minimal runner dependency lock resolves 10 hash-pinned packages matching tested
  versions; image installation/runtime validation remains pending.

AWS, LLM, and Terraform calls in these tests are mocked. Migration tests use
temporary fixtures. This is not evidence of a successful live deployment or a real
Terraform apply. HTTP TestClient needs execution outside the current sandbox;
an empty FastAPI TestClient also hangs inside it. HTTP fixtures block external sockets;
  the runner authentication test uses only a local loopback server.

The full embedding/runtime stack, real-container recreation/restore, manual browser
UI, live AWS permissions, and real Terraform execution have not been smoke-tested.
Docker daemon access was denied inside/outside the sandbox, so the new worker
image builds and dummy container smoke test could not run locally. Remote CI has
not been observed running. Model/data-dependent RAG tests and obsolete
RAG HTTP tests remain excluded from the default suite pending their planned update.

## Setup required before use


Complete the runner bootstrap/configuration in README before Terraform use. The
private token volume must be initialized before first startup, the job image ID
must be explicitly selected, and Docker Engine 26+/working resource controls and
subpath mounts must pass the dummy smoke check. Existing root-owned volumes and
legacy state require deliberate backed-up migration/recovery; none was performed.

1. Follow [README.md](README.md) to install the locked dependencies using Python 3.11
   and the frontend dependencies with `npm ci`.
2. Configure the operator in `backend/.env`. From the activated Python environment:

   ```bash
   cd backend
   python -m services.auth hash-password
   ```

   Put the generated hash in `ACA_OPERATOR_PASSWORD_HASH` and set
   `ACA_OPERATOR_USERNAME`. Do not use or expect the removed demo credentials.
3. Set exact `ACA_ALLOWED_ORIGINS`. Use `ACA_COOKIE_SECURE=false` only for local
   HTTP; use HTTPS and `true` for deployment. Restart after configuration changes.
4. Configure server-side AWS credentials/role/profile and any model keys. Temporary
   AWS credentials also need their session token. Optionally pin the account with
   `ACA_AWS_ACCOUNT_ID`. Never paste secrets into browser preferences.
5. For external clients, optionally run `python -m services.auth generate-token`;
   keep the raw token in the client secret store and configure only its digest.
6. Before upgrading existing containers, stop writers and export writable-layer
   history/Chroma data and named volumes. Follow the migration instructions in
   README **before** replacing containers; removed-container data cannot be recovered
   by the migration code. Do not remove persistent volumes during the upgrade.

Authentication endpoints: `/auth/login`, `/auth/session`, `/auth/logout`.
Authenticated configuration/identity endpoints: `/auth/config` and
`/auth/aws-identity?region=eu-west-1`. The MCP identity tool is
`aws_connection_identity`. Only probes and the constrained login flow are public.

## Remaining work, in order

8. Add durable background jobs, real progress, interruption handling, and reconciliation
   instead of automatically retrying uncertain mutations.
9. Fix filtered RAG retrieval/document replacement; evaluate retrieval; correct health
   scoring and cost comparison periods.
10. Complete integration/end-to-end coverage, logging/redaction, operational recovery,
    and sandbox-account deployment validation.

Initial fixes already address some false-clean paths, SSH/RDP port ranges, failed
agent plans reaching approval, Chroma's default path, and execution-ID validation.
Steps 5–7 implementation is complete; Steps 8–10 remain outstanding. Step 7
container-runtime acceptance is still pending as described above.

Other tracked findings: the build still warns about a roughly 662 kB uncompressed
JS bundle. The earlier npm installation reported 14 advisories (1 low, 2 moderate,
11 high); that count is historical, not a fresh dependency audit. Scan retention/
archiving and full runtime compatibility remain operational follow-ups.

## Continuation instructions

Step 7 implementation is finished; wait for approval before starting Step 8.
Keep the pending container build/smoke acceptance check visible in the next session. The latest code and
checkpoint updates are saved locally and have not been committed or pushed.
Pre-existing `PROJECT-ANALYSIS.md`
and `project-handoff/` remain local and were excluded from the published commit.

Use [to_improve.md](to_improve.md) for the ordered checklist and detailed per-step
history, and README for setup/compatibility/recovery instructions.

**Do not start Step 8 automatically.** The user's workflow is: before each step,
explain the current code, proposed changes, and expected benefit; implement only
after approval. After each step, explain the resulting code and verification,
offer to explain the next plan, and ask whether to proceed.

This status update only records current progress; it does not authorize additional
implementation, deployment, live infrastructure changes, or commits.

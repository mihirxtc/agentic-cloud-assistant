# Agentic Cloud Assistant — project status

Updated: **29 October 2026** (Europe/London).

## Current position

**Improvement steps 1–4 are implemented. Step 5 is next and has not started as a
complete work item.** Some scan correctness fixes were made during the initial
review, but the full coverage/unknown-state work remains outstanding.

This snapshot contains the completed local implementation; Git history records
its subsequent publication status. It has **not been deployed**.
No live AWS resources were changed, no live account identity was verified, and no
real operator password, bearer token, private `.env` file, or user database was
provisioned or migrated during this work.

The application remains a single-operator AWS management prototype. Authentication
and account binding are implemented; transactional approvals, isolated Terraform
workers, and durable background jobs are not yet implemented.

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

These Terraform checks are configuration guards, **not worker isolation** or a
complete resource ownership/security policy. Supported plans currently use root
resources and the standard HashiCorp `aws`, `random`, `tls`, and `local` providers.

## Current architecture and storage

- React/Vite dashboard → authenticated FastAPI/FastMCP → Python services → AWS,
  Terraform, Groq/Anthropic/Ollama, and Chroma retrieval.
- Docker/local HTTP startup shares one app factory; stdio shares the tool registry.
- SQLite is currently **schema version 3**, holding execution payloads, sessions,
  login throttling, and scan identity/service-status records.
- Upgrades from schemas 1/2 create SQLite backups before migration. Scan records
  retain provenance, not raw inventory or credentials.
- Docker volumes: `app_data` → `/app/data`, `chroma_data` → `/app/chroma_db`, and
  `terraform_workdirs` → `/app/terraform_workdirs`.
- Local defaults: `backend/data`, `backend/chroma_db`, and
  `backend/terraform_workdirs`. `ACA_DATA_DIR` and `CHROMA_DB_PATH` are configurable.

## Latest verification results

Last implementation verification, recorded on 29 October 2026:

- **144 backend tests passed**: rules, mocked providers, authentication, storage,
  migrations, HTTP/MCP routing, AWS identity, cache separation, and guarded execution.
- **17 frontend cases passed** across session, preference, and AWS identity tests.
- Frontend lint and production build passed.
- All **94 installed test-environment packages** passed `uv pip check`.
- Python compilation, Compose configuration validation, and diff whitespace checks passed.

AWS, LLM, and Terraform calls in these tests are mocked. Migration tests use
temporary fixtures. This is not evidence of a successful live deployment or a real
Terraform apply. HTTP TestClient needs execution outside the current sandbox;
an empty FastAPI TestClient also hangs inside it. HTTP fixtures block external sockets.

The full embedding/runtime stack, real-container recreation/restore, manual browser
UI, live AWS permissions, and real Terraform execution have not been smoke-tested.
Remote CI has not been observed running. Model/data-dependent RAG tests and obsolete
RAG HTTP tests remain excluded from the default suite pending their planned update.

## Setup required before use

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

5. Complete scan coverage: consistent complete/partial/failed assessments, per-item
   permission unknowns, port ranges/protocols/CIDRs, and accurate frontend presentation.
6. Enforce transactional execution transitions, immutable plan approval, idempotency,
   reviewed direct changes, and separately approved destroy plans.
7. Isolate Terraform in a non-root worker; complete execution/resource policy, retain
   lockfiles, harden paths, and fix the Terraform/S3 locking-version mismatch.
8. Add durable background jobs, real progress, interruption handling, and reconciliation
   instead of automatically retrying uncertain mutations.
9. Fix filtered RAG retrieval/document replacement; evaluate retrieval; correct health
   scoring and cost comparison periods.
10. Complete integration/end-to-end coverage, logging/redaction, operational recovery,
    and sandbox-account deployment validation.

Initial fixes already address some false-clean paths, SSH/RDP port ranges, failed
agent plans reaching approval, Chroma's default path, and execution-ID validation.
They do not establish that every remaining Step 5–10 requirement is satisfied.

Other tracked findings: the build still warns about a roughly 657 kB uncompressed
JS bundle. The earlier npm installation reported 14 advisories (1 low, 2 moderate,
11 high); that count is historical, not a fresh dependency audit. Scan retention/
archiving and full runtime compatibility remain operational follow-ups.

## Continuation instructions

Use [to_improve.md](to_improve.md) for the ordered checklist and detailed per-step
history, and README for setup/compatibility/recovery instructions.

**Do not start Step 5 automatically.** The user's workflow is: before each step,
explain the current code, proposed changes, and expected benefit; implement only
after approval. After each step, explain the resulting code and verification,
offer to explain the next plan, and ask whether to proceed.

This status update only records current progress; it does not authorize additional
implementation, deployment, live infrastructure changes, or commits.

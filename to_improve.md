# Project improvement plan

Goal: secure, accurate, recoverable AWS scan → assess → propose → approve → execute workflow.
Default: single operator, Docker Compose, one configured AWS account.

## Ordered checklist
- [x] 1. Establish locked dependencies, development tools, baseline checks and CI.
- [x] 2. Consolidate HTTP startup; persist Chroma and SQLite execution history; establish the durable database foundation for sessions/jobs/approvals/audit; migrate existing history with backup; add readiness.
- [x] 3. Replace browser-only login with server sessions and external MCP bearer authentication; protect every HTTP interface; remove browser secrets.
- [x] 4. Share verified STS account identity and credentials across all workflows; bind executions and caches to account and region.
- [ ] 5. Return complete/partial/failed scan coverage; prevent false-clean claims; preserve security-group port ranges and sources.
- [ ] 6. Enforce transactional execution transitions, saved-plan approvals, idempotency, reviewed direct changes and separately approved destroy plans.
- [ ] 7. Isolate Terraform in a non-root worker; enforce provider/HCL policy; retain lockfiles; validate paths; align Terraform and S3 locking versions.
- [ ] 8. Persist background jobs and real progress; handle interruptions without blindly retrying mutations.
- [ ] 9. Correct filtered RAG retrieval and document replacement; evaluate retrieval; correct health scoring and cost periods.
- [ ] 10. Update integration tests, exercise mocked end-to-end workflow, redact logs, document setup/backups/recovery; verify real changes only in a sandbox account.

## Acceptance
Unauthenticated operations blocked; incomplete scans never appear clean; repeated approvals rejected transactionally; isolated Terraform; container replacement preserves data; remediation verified by rescan.

## Interface changes
Session login/logout/status, readiness and job endpoints; scan coverage/identity/errors/timestamps; job IDs for long operations; authenticated approval; removal of browser credentials. Update bundled clients together.

## Verification log
### Step 1 — completed 2026-10-28

Before: unpinned backend dependencies, no default offline test configuration or CI,
missing local tooling. Initial baseline: 32/33 offline tests passed; frontend lint
reported 13 errors and 3 warnings; production build passed with a bundle warning.

Changes: generated Python 3.11 runtime/test locks from separate input files;
added default offline pytest selection, GitHub Actions backend/frontend checks,
and setup instructions in README.md. Matched frontend Docker/CI to Node 22.
Corrected an obsolete unsupported-model test fixture. Resolved lint failures by
separating shared hooks from component modules, synchronising refs outside render,
stabilising callback dependencies, and removing unused code/directives.

After: 33/33 tests pass under Python 3.11.16; all 13 installed test dependencies
pass `uv pip check`; backend compileall and git diff whitespace checks pass;
frontend lint passes without warnings and production build passes under local
Node 26.12.0. Node 22 execution will be checked by CI; CI has not been run remotely.

Limits: the full 173-package runtime lock was resolved but not installed or smoke
tested; model/data-dependent RAG tests and obsolete RAG HTTP tests are explicitly
excluded from the default suite pending their planned update. Frontend npm ci
reported 14 advisories (1 low, 2 moderate, 11 high); upgrades remain separate work.
The production bundle still emits a size warning (~653 kB uncompressed JS).
No AWS mutations, model calls, or existing private configuration inspection.

### Step 2 — completed 2026-10-28

Before: main:app and mcp_server.py built different HTTP apps; Docker lacked
Ollama HTTP routes. Execution history was rewritten as JSON outside any volume;
Chroma's legacy location could be outside its volume. Health only checked uptime.

Changes:
- Shared http_app.create_app serves both HTTP entrypoints; MCP stdio remains
  separate and reuses one registered server instance.
- SQLite schema version 1 stores existing execution payloads transactionally
  under ACA_DATA_DIR/aca.sqlite3. The same database will gain session/job/approval/
  audit schemas with steps 3, 6 and 8, rather than inventing unused schemas now.
- JSON import saves a byte-identical hash-named backup before parsing, preserves
  IDs and order, rolls back invalid imports, and records one-time completion.
  Storage failures and unknown updates now surface errors instead of empty results.
- Chroma migration backs up a stopped legacy collection, stages a copy, and only
  activates it into an empty destination; populated destinations are not merged.
- Added app_data volume and explicit data/Chroma paths; sensitive runtime files
  are excluded from git and image build contexts.
- /health reports liveness; /ready reports local SQLite, directory, Terraform and
  knowledge-base readiness without AWS, embedding or LLM requests. Compose gates
  frontend startup on /ready. Both proxies expose readiness and Ollama routes.
- Removed automatic startup workdir purging because remote state can exist without
  a local tfstate file. Added upgrade/export/backup/restore instructions in README.

Verification: 60 offline tests pass (33 existing plus 27 storage/HTTP tests),
including concurrent updates, restart persistence, corrupt-input rollback,
Chroma backup/copy failure handling, entrypoint parity, MCP initialization,
HTTP/stdio script selection, upload, key download and streamed Ollama output.
All 91 installed test packages pass uv pip check. Python compilation, Compose
configuration validation, git diff whitespace, frontend lint and build pass.
HTTP TestClient needs execution outside this sandbox: an empty FastAPI app also
hangs inside it and succeeds outside. HTTP test fixtures block external sockets.

Limits: migration was exercised against temporary fixtures, not existing user
records. No live data was migrated, containers recreated, AWS actions executed,
or real embedding stack started. Actual container recreation/restore remains a
deployment acceptance check. Existing frontend bundle/advisory findings remain.
Old-container writable-layer data must be exported before replacing that container;
code cannot recover removed-container data. Runtime Chroma errors are logged and
reported as not ready. Authentication and approval enforcement remain later steps.

### Step 3 — completed 2026-10-28

Before: React checked a hardcoded username/password and browser login flag; HTTP
MCP/REST operations had no application identity checks. Cloud and provider secrets
were persisted in browser storage and passed through dashboard tool calls.

Changes:
- Added server-configured single-operator identity, salted PBKDF2-SHA256 password
  hashes (600,000 iterations), an interactive hash command and no default password.
- SQLite schema v2 adds hashed session tokens, CSRF tokens, absolute expiration
  and transactional login throttling. Upgrading v1 saves a SQLite backup first.
- The HTTP middleware guards MCP, REST wrappers, docs, uploads, downloads and
  configuration. Only health/readiness probes and the constrained login endpoint
  are public. Unconfigured auth fails closed; stdio retains its local boundary.
- HttpOnly, SameSite=Strict, host-only cookies; Secure and __Host- prefix in HTTPS
  mode. Enforced CSRF for cookie mutations and exact allowed Origin checks.
- Added login/session/logout/config endpoints, server-side revocation, login
  rotation and credential-change invalidation. Session-storage failures deny access.
- Optional external bearer authentication stores only a token digest in server
  config and grants full operator access; machine tokens rotate independently.
- Browser session/CSRF state now stays in memory; refresh restores via the cookie.
  Logout is confirmed server-side and 401 responses reset the UI. Same-origin fetch
  prevents credential/CSRF leakage. MCP paths are normalized to avoid redirects.
- Removed demo credentials and secret fields; legacy browser secrets/login flags
  are purged and only model/region preferences migrate. Settings reports non-secret
  server configuration flags. Frontend calls no longer send cloud/model keys.
- Private responses disable caching; validation errors omit rejected input.
  Added auth proxies, environment examples, setup/revocation instructions and CI
  checks for frontend session/preference tests.

Verification: 97 backend tests pass; all 12 frontend session/preference cases
pass (8 session and 4 preference tests, also checked directly with Node).
Frontend lint/build, Python compilation, dependency compatibility,
Compose configuration validation and whitespace checks pass. Test data is isolated
and cloud/model calls are blocked. Tests cover anonymous denial across HTTP entry
points, cookies, expiration, revocation/replay, CSRF/origins, bearer rotation,
credential changes, rate limits, secret redaction, persistent sessions, v1 backup
migration, legacy browser-key removal and stale-response/session races.

Setup required: the real operator must generate a password hash and configure
ACA_OPERATOR_PASSWORD_HASH before browser login. No real password, bearer token,
private .env file, user database or deployment was provisioned/changed. Use
ACA_COOKIE_SECURE=false only for local HTTP; configure true and an exact HTTPS
origin for deployment. No manual browser UI or real-container smoke test was run.
Existing bundle/advisory findings remain. Account binding, approval state
transitions and Terraform isolation remain pending steps.

### Step 4 — completed 2026-10-29

Before: scanning/cost helpers constructed separate boto3 clients; several tools
still accepted caller credentials. Terraform omitted temporary session tokens.
Plans lacked verified account/region metadata, direct fixes had no scan binding,
and chat caching guessed identity from an environment access-key prefix.

Changes:
- Added shared AWSConnection verification using the server credential chain,
  frozen credentials and STS GetCallerIdentity. A ContextVar reuses that exact
  snapshot across each workflow and asyncio.to_thread; no global credential
  environment is modified. Identity responses omit all credential material.
- Added optional ACA_AWS_ACCOUNT_ID pinning, bounded STS request timeouts and
  safe verification errors. Caller-supplied AWS keys were removed from all tools
  and services; REST wrappers reject unknown fields rather than ignore them.
- All AWS MCP tools/resources now verify at their operation boundary and return
  account, partition, region, principal ARN and verification time. Cost Explorer
  remains account-wide through its standard endpoint, not a regional spend filter.
- Schema v3 persists scan ID/identity/service statuses, preserving prior history
  and sessions with a pre-upgrade v1/v2 SQLite backup. Findings carry scan IDs.
- Plans/agent actions persist identity. Apply, agent approval and destroy compare
  fresh server identity with the recorded account/partition/region before action;
  omitted action regions use the record. Legacy unbound executions are blocked
  for mutation, but remain readable/rejectable without AWS connectivity.
- Direct security-group fixes require a recorded successful SG scan and matching
  identity. Browser calls include expected account ID to reject unnoticed server
  credential switches. Cached context is scoped by partition/account/region/ARN;
  mutation attempts invalidate all regional entries for the affected account.
- Terraform subprocesses receive frozen keys/session tokens and region, clear
  stale profile/token selectors and disable shared-file credential fallback.
  Root default AWS provider and generated S3 backend get allowed-account guards;
  guard files are checked before apply/destroy. TF_VAR_region is set consistently.
- Added pinned python-hcl2 validation before Terraform execution: reject provider
  credential overrides/aliases/role assumptions/custom endpoints, skipped checks,
  unsupported providers, modules, embedded state/cloud settings, provisioners,
  and conflicting AWS resource/provider regions. Supported providers remain the
  existing root aws/random/tls/local set. This is not worker isolation.
- Added authenticated /auth/aws-identity and aws_connection_identity MCP tool.
  The UI verifies on login/region change or demand, displays account and region,
  clears stale views and prevents unverified AWS actions while retaining history
  and knowledge-base access. History labels legacy identity as unverified.
- Updated setup/compatibility instructions and locked parser/test dependencies.

Verification: 144 backend tests pass (97 before this step; 47 added cases).
All 17 frontend cases pass across session/preference/identity test files, including
five new identity tests checked directly with Node. Frontend lint/build, 94-package
uv pip check, Python compileall, Compose config validation and git diff whitespace
checks pass. Tests cover STS/frozen credentials, temporary-token propagation,
wrong-account/region/legacy denial before mutation, successful mocked apply/destroy,
provider bypass rejection before init, failed-scan rejection, async propagation,
cache separation/invalidation, metadata-aware assessment, schema migration,
HTTP authentication, safe errors, UI request binding and late-response races.

Limits: all AWS/LLM/Terraform calls in these tests are mocked; no live account was
verified, no user records migrated, no private .env changed and no deployment was
started. Full embedding/runtime and real-container/browser/Terraform smoke tests
remain outstanding. Existing bundle/advisory findings remain. Approval-state
transactions, resource ownership policy and isolated workers are still later work;
account binding alone does not make Terraform a sandbox. Scans retain provenance
without raw inventory; retention/archiving policy remains future operational work.
Legacy resources may need manual recovery/import in their original account; do
not fabricate identity metadata to force approval. The pre-existing Terraform/S3
locking-version mismatch remains in the later Terraform hardening step.

Next: step 5 — accurate scan coverage and complete/partial/failed assessments,
including preserving ports, protocols, CIDRs and unknown permission results.
Offer to explain the next plan and wait for approval before implementing it.

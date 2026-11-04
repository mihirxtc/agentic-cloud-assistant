# Project improvement plan

Goal: secure, accurate, recoverable AWS scan → assess → propose → approve → execute workflow.
Default: single operator, Docker Compose, one configured AWS account.

## Ordered checklist
- [x] 1. Establish locked dependencies, development tools, baseline checks and CI.
- [x] 2. Consolidate HTTP startup; persist Chroma and SQLite execution history; establish the durable database foundation for sessions/jobs/approvals/audit; migrate existing history with backup; add readiness.
- [x] 3. Replace browser-only login with server sessions and external MCP bearer authentication; protect every HTTP interface; remove browser secrets.
- [x] 4. Share verified STS account identity and credentials across all workflows; bind executions and caches to account and region.
- [x] 5. Return complete/partial/failed scan coverage; prevent false-clean claims; preserve security-group port ranges and sources.
- [x] 6. Enforce transactional execution transitions, saved-plan approvals, idempotency, reviewed direct changes and separately approved destroy plans.
- [x] 7. Isolate Terraform in a non-root worker; enforce provider/HCL policy; retain lockfiles; validate paths; align Terraform and S3 locking versions. Implementation and published Step 7 CI image/isolation smoke acceptance complete.
- [x] 8. Persist background jobs and real progress; handle interruptions without blindly retrying mutations. Core implementation published; ambiguous outcomes remain locked for validated offline recovery.
- [x] 9. Correct filtered RAG retrieval and document replacement; evaluate retrieval; correct health scoring and cost periods. Implemented locally; see the validation and unrelated Ollama-test caveat below.
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


### Step 5 — completed 2026-11-03

Before: failed services suppressed findings from successful services. S3/IAM
inspection exceptions became false/empty values; bucket policy and account BPA
were not inspected. The UI displayed missing inventory as zero, assigned scores
to incomplete assessments, and claimed a fix was confirmed from a zero count.

Changes:
- Shared complete/partial/failed coverage for full scans, assessment tools and
  findings resources; retain service/rule errors, unknown-resource IDs, timestamps,
  verified identity and scan ID. SQLite retains this coverage with provenance.
- Retain observed inventory through interrupted pagination and confirmed findings
  from successful checks. Detail errors use null/unknown instead of false/zero.
- Paginate S3/IAM detail collections. Check bucket policies, ACL public grantees,
  and bucket/account public-access blocks. Report the limited configuration scope;
  do not claim anonymous object readability or coverage of object ACLs/access points.
- Preserve SG range endpoints, protocol, both CIDR families and other ingress
  sources. Findings include world-open sources; matching/revocation share protocol
  semantics (TCP SSH, TCP/UDP RDP, all protocols), excluding ICMP type/code matches.
- Preserve unknown VPC subnet counts and IAM details; distinguish console-use
  history from API activity and do not flag new never-used users as 90-day inactive.
- Display coverage/errors and unknown inventory, suppress incomplete health scores,
  keep partial findings visible, block agent planning on incomplete coverage, and
  replace unsupported fix-confirmation claims with scoped re-scan feedback.
- Keep confirmed findings when model summary generation fails; incomplete/no-finding
  summaries are deterministic and do not claim the account is secure.

Verification: 170 offline backend tests passed (26 added cases); 20 frontend cases
passed across four files. Frontend lint/build, Python compilation and whitespace
checks passed. Regressions cover permission denial, interrupted pages, mixed-service
findings, unknown MFA/public access/subnets/key dates, effective account/bucket BPA,
S3 policies, SG IPv6/ranges/protocols, malformed rules, ENI failure, blocked agent
planning and model-summary failure. All AWS/model calls were mocked.

Limits: no live AWS changes, deployment, manual browser validation, or real Terraform
execution. Existing bundle warning (~658 kB) and historical npm advisory findings
remain. Health-score weighting is still Step 9; incomplete scores are now hidden.
Approval transactions and isolated Terraform execution remain Steps 6–7.
Steps 1–4 are on GitHub at 21813ae; Step 5 and this checkpoint are local,
uncommitted and unpushed.

Next: Step 6 — transactional execution transitions, immutable saved-plan approvals,
idempotency, reviewed direct actions and separately approved destroy plans.
Explain the plan and obtain approval before implementing Step 6.

### Step 6 — implementation plan (approved; completed below)

Before Step 6: SQLite updates were atomic, but approval does not atomically claim an
awaiting execution before AWS/Terraform starts. Approvals identify an execution
without binding the reviewed plan bytes. Direct ingress removal mutates AWS
immediately; the rollback endpoint executes an unreviewed destroy. Timeouts are
reported as ordinary failures despite potentially uncertain remote effects.

Implement in this order:
1. Add transactional execution-state transitions and transition history. Atomically
   claim an awaiting action before mutation; serialize actions sharing a Terraform
   workspace. Reject invalid transitions and conflicting concurrent approvals.
2. Bind approval to a persisted digest of the saved plan and relevant configuration,
   action type, verified account/region, and review metadata. Verify the digest
   before execution; changed/missing plans require a new review. Existing proposals
   without this evidence remain readable/rejectable but cannot execute.
3. Add idempotency keys tied to action/request content. Repeated requests return
   the recorded operation/result; a reused key with different content is rejected.
   In-progress or uncertain actions cannot automatically execute again. Record
   timeout/interruption uncertainty without claiming rollback or remote success.
4. Replace immediate SG mutation with proposal/review/approval of the exact rules
   and sources to revoke. Recheck for drift before mutation. Route manual and agent
   direct fixes through the same claimed execution path.
5. Replace immediate rollback/destroy with a separately generated saved destroy
   plan and explicit approval. Keep the original workspace/state identity, serialize
   against other actions there, and apply only the reviewed destroy plan.
6. Update React and MCP/REST schemas together: display action/account/region and
   reviewed changes, submit approval digest/idempotency key, and surface conflicts,
   rejection, in-progress and uncertain results accurately.
7. Add offline regressions for simultaneous/duplicate approvals, approval/rejection
   races, changed plans, request-key conflicts, SG rule drift, rejected actions,
   separately approved destroy, and uncertain outcomes. Run backend/frontend checks
   and update this log and status.md.

Expected result: one claimed action per approval, tied to the reviewed content;
retries cannot silently repeat mutations, and destruction requires its own review.
Worker isolation/resource policy remain Step 7; durable job recovery/reconciliation
remain Step 8. No live AWS changes or deployment are part of this implementation.


### Step 6 — completed 2026-11-03

Before: identity was checked, but approvals could race or repeat without an atomic
claim; review was not bound to saved bytes. Manual ingress fixes mutated on request,
and rollback immediately ran destroy. Interrupted mutations appeared as simple failures.

Changes:
- SQLite schema 4 adds idempotency requests, target locks and transition events;
  upgrades from schemas 1–3 create SQLite snapshots before migration.
  Atomic claims/transitions reject conflicting approvals/rejections and repeated
  mutation attempts. Generic updates cannot rewrite sealed action state/content.
- Shared action_service proposals bind account/region/action/review output and
  saved plan/config/lockfile hashes or exact SG permissions to an approval digest.
  Missing, altered or legacy unsealed plans cannot execute. Artifact/permission
  verification happens after the claim and before dispatch; drift invalidates.
- Same-key/same-content retries return persisted results or in-progress state;
  different content under a reused key is rejected. Failed/uncertain/crash-left
  actions remain blocked, with target locks retained where remote effects are unknown.
- Manual and agent SG fixes now prepare exact public-rule proposals, then use the
  same executor to submit only reviewed permissions after checking drift.
  Removed the unused immediate-revocation implementation.
- Rollback now prepares an independently reviewed saved destroy plan in the
  original workspace/state. Only its separate digest-bound approval applies it;
  no public path runs terraform destroy directly.
- React supplies stable decision keys scoped to each action and displays review
  identity/digest/content. Direct fixes and destroy use explicit review panels;
  history supports pending reviews and shows actor/time/state events. Lost mutation
  responses are shown as uncertain, not confirmed failure/success.
- Reviewed plan/provider artifacts are retained as evidence. Authentication context
  reaches audit events over both REST and mounted MCP, including worker threads.

Verification: 198 offline backend cases and 23 frontend cases passed. Backend
coverage includes concurrent duplicate approval, approve/reject races, request-key
conflicts, changed/missing plans/configuration, SG drift and exact permissions,
separate destroy approval/workspace locks, rejected/legacy actions, unknown outcomes,
restart persistence, schema-3 backup/session preservation, and HTTP actor attribution
on REST/MCP. Frontend lint/build, Python compilation and whitespace checks passed.
All AWS/model/Terraform operations were mocked; no live mutation or deployment.

Limits: trusted-host integrity checks are not worker isolation or a complete resource
policy (Step 7). Failed/uncertain and crash-left locks deliberately require recovery;
there is no automatic unlock/retry or durable reconciliation workflow yet (Step 8).
The bundle warning is ~662 kB; historical npm advisories remain. No live/manual
browser/Terraform deployment validation. Steps 5–6 and this checkpoint are saved
locally, uncommitted and unpushed; GitHub main still contains Steps 1–4 at 21813ae.

Next: Step 7 — isolated non-root Terraform execution, resource/provider policy,
lockfile/path hardening and Terraform/S3 locking-version alignment. Explain the
concrete plan and obtain approval before implementing Step 7.

### Step 7 — implementation plan (approved; completed below)

Before Step 7: Terraform ran as a subprocess in the web backend container,
which has no non-root USER declaration. Its environment starts from os.environ,
so unrelated application secrets/settings can reach provider processes. Provider
allowlisting does not restrict resource types, resource ownership, local file
paths or HCL file-reading functions. Workdir creation follows filesystem links,
and planning deletes lockfiles/clears a shared cache on checksum failures. The
image pins Terraform 1.8.5 while server-generated S3 backend configuration uses
native use_lockfile locking. Approval digests from Step 6 remain required.

Proposed implementation order:
1. Introduce a narrow execution-runner boundary. The web backend sends authenticated,
   fixed-operation requests (init/plan/apply-saved-plan/show) and cannot select shell
   commands, images, arbitrary mounts or container privileges. A trusted runner
   launches per-job non-root containers with a pinned Terraform image. Keep the
   container-engine socket out of the web backend and Terraform job containers;
   any engine access belongs only to the deployment-managed runner. No in-process
   or host-Terraform fallback when the runner is unavailable.
2. Restrict each job to its own workspace, with a read-only root filesystem,
   temporary scratch storage, dropped capabilities, no new privileges and CPU,
   memory, PID/output/runtime limits. Exclude app data, Chroma, .env files, source
   trees and unrelated workspaces. Use the runner's fixed network/egress policy
   for required AWS/provider endpoints. Do not change the host Docker daemon as
   part of application code changes.
3. Construct job environments from an explicit allowlist. Pass only the frozen,
   verified operation credentials and required runtime/region settings. Do not
   copy backend model keys, login hashes, bearer tokens, arbitrary TF_CLI_ARGS,
   TF_VAR values or provider configuration. Keep credentials out of persisted
   requests/artifacts/logs and verify account/region at the worker boundary.
4. Add an explicit supported resource/action policy, checked before init and against
   structured saved-plan changes before approval. Deny unreviewable/unsupported
   resource types, executable/external data sources, provisioners, filesystem reads
   and writes outside permitted artifacts, credential/provider bypasses and
   unapproved changes to existing resources. Require proven ownership or configured
   explicit target authorization. Unknown policy-relevant values fail closed.
   Update generation prompts to match the supported surface; this may reject HCL
   the current prototype accepts. Constrain SSH key artifacts to approved paths.
5. Centralize safe workspace/artifact access. Validate IDs and filenames, reject
   symlinks/escapes and unsafe file types, and use bounded regular-file operations
   for saved plans, state and key downloads. Preserve Step 6 approval digests,
   workspace serialization and independently reviewed destroy plans across the
   runner boundary. Legacy artifacts need validation/replanning before execution.
6. Pin a Terraform release compatible with native S3 lockfiles; verify downloaded
   release checksums and supported CPU architectures. Pin provider requirements,
   retain dependency lockfiles, verify them on execution, and remove shared-cache
   deletion/checksum-retry behavior. Update S3 state/lock IAM setup instructions;
   do not create a backend bucket or change IAM permissions automatically.
7. Update Compose, readiness, local setup and upgrade docs. Separate API readiness
   from runner availability, document volume ownership/migration and retained-state
   recovery, and preserve existing data rather than replacing volumes. Add offline
   tests for policy/path/env escapes, runner authentication, fixed operations,
   digest/ownership checks and unavailable-runner denial. If the container runtime
   is available, smoke-test non-root/resource/mount isolation using dummy inputs,
   without AWS credentials or live infrastructure changes. Report any unavailable
   runtime/build verification explicitly.

Expected result: reviewed Terraform runs outside the web app, with bounded job
access, explicit resource policy and reproducible engine/provider artifacts.
Container isolation remains a security boundary with a trusted runner/host;
this does not claim a complete sandbox against host/container-runtime compromise.
Durable scheduling, interrupted-job reconciliation and controlled recovery remain
Step 8. Implementation and any host setup/deployment are not authorized by saving
this proposed plan; obtain approval to implement Step 7 first.

References: HashiCorp S3 backend and provider-lockfile documentation; Docker run
security controls and Docker Engine trust-boundary documentation linked in the
Step 7 explanation. Resolve and record exact engine/provider versions during
implementation rather than assuming current releases.


### Step 7 — implementation completed 2026-11-04; container validation pending

Before: Terraform and syntax validation launched subprocesses in the web container,
which inherited application environment settings. Planning removed lockfiles and
could delete a shared provider cache. Identity checks did not constrain resource
ownership/filesystem access; Terraform 1.8.5 mismatched native S3 lock configuration.

Changes:
- Removed Terraform/provider execution and workspace mounts from the non-root web
  API. A private authenticated runner accepts fixed operations only, with no local
  fallback or caller-selected image/commands/mounts. Artifact/key access uses that
  boundary too. API readiness is local; authenticated /ready/terraform is separate.
- A trusted runner launches per-job UID/GID-10001 containers with one workspace
  subpath, read-only root, tmpfs, dropped capabilities/no-new-privileges, bounded
  CPU/memory/PIDs/output/deadlines and an internal network. An AWS-HTTPS CONNECT
  proxy checks resolved public addresses; direct routing/DNS is restricted by job
  configuration. The engine socket exists only in the trusted runner.
- Frozen credentials travel over stdin, not Docker argv/environment configuration
  or request files. The job re-verifies STS identity. Terraform children receive
  an allowlisted environment without application/model/login secrets or CLI injection.
- Added a fail-closed resource policy before init and against structured saved-plan
  changes before approval/apply. Supported scope: private safe S3 resources, bounded
  private VPC/subnets, explicit TCP/CIDR SGs, approved small private EC2/AMIs, key
  pairs, random suffixes and a controlled TLS key. Unsupported resource types,
  executable/file access, imports/modules/data, user-data programs, unapproved
  existing targets, replacements and critical unknowns are rejected.
- Runner-owned resource IDs/tags prove update/delete ownership. Private SSH key
  references cannot flow through locals/tags/user exports; only public attachment
  and the fixed sensitive ssh-key.pem export are supported.
- Bounded no-follow regular-file access rejects path escapes, symlinks, hardlinks,
  FIFOs and extra config files. Manifests bind files, canonical provider lock,
  immutable image ID, policy and ownership ledger; both API and job verify them.
  Legacy workspaces are preserved without automatic adoption/execution.
- Persisted runner one-shot dispatch evidence before launch, including fsync of
  the metadata directory. Lost responses and failed/uncertain operations cannot
  replay a saved plan; Step 6 approval/target serialization remains intact.
- Pinned Terraform 1.17.1 and aws 5.100.0/random 3.7.2/tls 4.1.0 archives with
  published amd64/arm64 SHA-256 values. Canonical locks come from checked image
  mirrors and runtime init uses -lockfile=readonly. Removed cache-deletion retries.
  The runner uses the official pinned Docker CLI 29.9.3; Engine 26+ is checked
  before each job, with no full-volume fallback for older engines.
- Added hashed 10-package minimal runner lock constrained to tested dependencies,
  private non-root token-volume bootstrap, Compose/loopback-development setup,
  migration/ownership/backup instructions and native S3 lock IAM documentation.
  The legacy S3 helper now only prints instructions; it does not create resources
  or edit private configuration. Nginx timeouts match bounded runner operations.
- Added CI image builds and a dummy-input container isolation smoke tool checking
  UID, mounts, read-only root, resource limits and direct network/DNS denial.

Verification: 273 offline backend cases passed; 23 frontend cases, frontend
lint/build, Python compilation, both Compose configuration checks, shell syntax
and whitespace checks passed. Application action tests use a fake runner transport;
runner tests exercise actual policy/path/protocol logic with mocked engine/AWS
boundaries and a loopback-only API authentication test. Tests include drift,
identity/ownership checks, provider locks, one-shot dispatch, old-engine denial,
private-key alias/exfiltration refusal, safe key recovery after policy/image changes,
output/token limits and no-host-execution behavior. No live AWS/model/Terraform calls.

Runtime acceptance remains pending: docker info failed with Unix socket permission
denied both inside and outside the sandbox. Consequently no job/runner/egress images
were built here, no container smoke test ran locally, and no deployment/volume
ownership migration or real token initialization occurred. The new CI isolation
job is configured but has not been observed running. Complete its image/smoke
checks in an authorized engine environment before treating isolation as runtime
validated. No host Docker configuration or permissions were changed.

Limits: the runner/engine/host remain trusted; containers are not a complete sandbox
against kernel/engine compromise. Resource support is deliberately narrower and
legacy execution requires separately validated recovery/migration. Durable jobs,
progress and reviewed reconciliation remain Step 8. Full embedding/browser and
live sandbox-account acceptance remain outstanding. Existing ~662 kB bundle warning
and historical npm advisories remain. Steps 5–7 are local, uncommitted and unpushed;
GitHub main still contains Steps 1–4 at 21813ae.

Next: explain Step 8 (durable jobs, real progress and controlled reconciliation)
and obtain approval before implementing it. Retain the pending Step 7 container
acceptance check in the handoff; do not claim it passed.

## Publication checkpoint — 4 November 2026

Steps 5–7 were committed as `26a2522` and successfully pushed to GitHub `main`.
Earlier uncommitted/unpushed statements above describe their historical checkpoints.
Frontend tests, lint and production build passed again before publication. Backend
tests were not rerun because the previous temporary Python environment no longer
exists; the last recorded result remains 273 passing tests. Step 7 container runtime
acceptance is still pending, and Step 8 has not started. Pre-existing analysis and
handoff exports remain local.

## Step 8 — durable jobs and controlled recovery — 4 November 2026

Implemented after the user approved the simplified explanation. Long tools queue
persisted jobs; a separate Compose/local worker runs the fixed operation registry.
SQLite schema 5 backs up schema 4 before migration and preserves review-only legacy
in-flight actions. Atomic worker claims/heartbeats fence stale status writers;
approval and execution enqueue commit together, as do queued cancellation and
revocation of its approved dispatch. Account/region is reverified with fresh
server credentials; submitter identity crosses the process boundary without tokens.

Real stage events replace timed agent steps. Browser polling and Background Jobs
restore progress/results after reconnect; old proposal results reflect current
action state. REST/MCP job APIs share authentication. Cross-process cache revision
markers invalidate account context after mutations. A separate worker readiness
probe distinguishes an available API from a missing worker.

The private runner saves apply terminal outcomes durably. Read-only inspection
and digest-bound confirmation can restore a lost successful completion without
replay; active jobs cannot be unlocked. Ambiguous/partial Terraform results and
interrupted direct SG mutations remain locked for validated offline recovery.
Queued work can be cancelled, including an approval not yet claimed. Running work
is not forcibly cancelled. No external AWS exactly-once guarantee is claimed.

Published Step 7 CI was checked and all three image builds plus dummy container
isolation smoke passed: https://github.com/mihirxtc/agentic-cloud-assistant/actions/runs/41772479505.
Step 8 changes were committed as `4df1cc6` and pushed to GitHub `main` on
4 November 2026. Full Compose restart/browser
and live AWS acceptance are not performed. See status.md for latest verification.
Next is Step 9; explain its plan and obtain approval before starting it.

## Step 9 — retrieval, scoped scoring and billing periods — 4 November 2026

Implemented at the user's request. Query-time resource predicates now constrain
Chroma's candidate set, including canonical/legacy UI labels. Same-ID document
updates use process/filesystem locking and a durable rollback journal, with stale
tail removal, exact-identity deletion and fail-closed interruption recovery.
Class imports no longer open a real knowledge database; encoder imports are lazy.

Added a repeatable temporary-database retrieval evaluation and a curated seven-query
regression corpus. Deterministic and real MiniLM ONNX CPU runs returned hit@1,
recall@3 and MRR 1.0, negative accuracy 1.0 and zero resource-filter leakage. This
small corpus is a baseline, not broad production/model quality certification.
Actual pinned Chroma tests cover filters, replacement/restart and cross-process reads.

Scoring now lives in the backend, weights applicable fully observed rule families
once and excludes empty scope; unknown/incomplete/legacy cases receive no score.
Billing preserves explicit unknown/zero/currency/estimated states, completes all
pages, separates UTC month-to-date from completed-month history and only compares
contiguous completed months. UI, tool/resource and chat contracts agree. Failed
model summaries retain billing results; unavailable data never implies zero spend.

Verification: 332 unaffected backend cases passed with two optional real-Chroma
checks skipped in the minimal environment; all 19 knowledge tests passed separately
with pinned Chroma. Frontend tests/lint/build, source compilation and dependency
checks passed. An unrelated concurrent Ollama-test edit causes a syntax error in
default discovery and was preserved pending the user's preference; see status.md.
No live cloud/billing/LLM calls, deployment or existing operator data migration.
Changes are local/uncommitted/unpushed. Next is Step 10 after explanation/approval.

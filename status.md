# Agentic Cloud Assistant — project status

Updated: **4 November 2026** (Europe/London).

## Current position

**Improvement steps 1–8 are implemented. Step 9 has not started.**
Step 8 was implemented after user approval and is local, uncommitted and unpushed.
Steps 1–7 are published on GitHub `main`: implementation commit `26a2522`, followed
by publication notes in `5108106` (Steps 1–4 originally landed in `21813ae`).

The published Step 7 container acceptance check is now verified: all three image
builds, the pinned Docker CLI check and the dummy isolation smoke test passed in
[GitHub Actions](https://github.com/mihirxtc/agentic-cloud-assistant/actions/runs/41772479505).
Backend/frontend checks also passed in that run. This closes the earlier pending
CI check; it is not a deployment or a real AWS/Terraform apply.

The app remains a single-operator AWS management prototype. Step 8 adds durable
jobs and conservative recovery; uncertain external outcomes are never blindly
retried. No deployment, live AWS changes, real credential provisioning, existing
volume migration or infrastructure execution was performed during this work.

## Implemented foundation

1. Locked Python dependencies, Node 22 frontend/CI, offline tests, shared setup docs.
2. Shared HTTP application, durable SQLite history, backed-up legacy import,
   explicit Chroma paths/migration and persistent Compose volumes/readiness.
3. Server password hashing, expiring/revocable sessions, CSRF/origin checks,
   optional external-client bearer auth and removal of browser secrets.
4. Frozen verified server AWS credentials and STS identity, account/region binding
   for tools/actions/scans/caches, and HCL configuration guards.
5. Explicit complete/partial/failed coverage, unknown-resource handling, pagination
   preservation and matching SG range/protocol/source assessment/remediation.
6. Transactional approvals and action events, saved-artifact/digest review binding,
   idempotency and locks, reviewed direct ingress proposals and separate destroy plans.
7. Private fixed-operation runner; non-root constrained Terraform job containers;
   pinned tools/providers and explicit resource/HCL policy; bounded artifact access;
   durable one-shot dispatch ledger, token bootstrap and operational migration docs.
8. Persistent background jobs, a separate worker, stage progress and restart
   handling, authenticated status/cancellation, and evidence-bound reconciliation.

## Step 8 behavior

- Long scan/security/cost/generation/agent/plan tools return a job descriptor.
  The browser follows its ID; external MCP/REST clients must do the same.
- Approval and execution enqueue are one SQLite transaction. Workers do not claim
  an action until it was approved. Cancellation of an approved but unclaimed job
  atomically rejects its dispatch and releases its lock. Running work is not
  forcibly cancelled and cancellation never undoes AWS changes.
- Workers use 10-second heartbeats and 60-second leases. Expired jobs never requeue
  automatically. Saved action results can restore lost completion responses;
  otherwise read/planning work becomes interrupted and mutations need reconciliation.
- Worker credentials are resolved fresh and account/partition/region are reverified.
  Actor identity persists without browser session tokens or server model/AWS keys.
- Real stages replace the agent's timer. Background Jobs lets an operator reopen
  results and reviews after reconnect. Proposal views reflect current action state.
- API context-cache invalidation crosses processes through account-specific SQLite
  revision markers. Worker readiness is separate from API/runner readiness.
- Runner apply outcomes persist in the private ledger. Inspection does not mutate
  infrastructure. Explicit digest-bound confirmation can restore a durable known
  success, provided its job is no longer active, without replaying its saved plan.
- Ambiguous/partial applies, legacy ledger entries without terminal evidence and
  interrupted direct SG calls remain locked. They require validated offline
  recovery after stopping/inspecting original processes and preserving backups.
  Current SG permissions alone cannot prove an older API call has terminated.

## Architecture and storage

React/Vite → authenticated FastAPI/FastMCP → SQLite job queue → background worker
→ AWS/model/RAG services or private runner → isolated Terraform job containers.
Short identity/history/validation/chat/RAG operations keep their current interfaces.
MCP stdio shares the registry and requires a separately running worker for jobs.

SQLite is **schema version 5**: history, sessions, throttling, scan provenance,
action requests/locks/events, jobs/submission keys and worker/cache metadata.
Existing schemas 1–4 get SQLite backups before migration. Legacy in-flight actions
are visible for review and never automatically adopted by a worker.

The API and background worker mount app_data/chroma_data plus the private runner
credential read-only. Neither has an engine socket or Terraform workspace mount.
Only the trusted runner mounts terraform_workdirs/runner_control and the engine;
Terraform containers get one workspace subpath and no application credentials.

Scan provenance still excludes raw inventory. **Completed job results can contain
raw inventory and model output**, and job inputs may contain sensitive HCL/request
text. Protect database backups and monitor size; automated job-result retention is
not yet implemented. Server credentials and model/session tokens are not copied
into job payloads.

## Verification

Step 8 local verification: **297 backend tests passed**; frontend tests, lint and
production build passed. The dependency environment passed its 94-package check.
Python compilation, whitespace checks and both Compose configurations passed
(using a temporary env-file override because no private backend/.env exists).

New tests cover durable submission, duplicate/conflicting request keys, atomic
approval enqueue/cancellation, competing workers, expired claims and stale writes,
fresh identity checks, submitter attribution, HTTP/MCP queue boundaries, restart
result recovery, schema-4 backup/import, cross-process cache revisions, persisted
runner outcomes and evidence-bound reconciliation. Frontend tests cover progress,
reconnect/error behavior and uncertain results without resubmission.

Backend tests use mocked AWS/model/engine boundaries and temporary databases.
HTTP tests run outside the sandbox due the previously established TestClient hang;
fixtures prohibit external network access. No real Terraform apply or AWS mutation.

Published Step 7 image/smoke checks passed remotely. Step 8 has not been published
or built into deployment images. Full Compose recreation/restart, manual browser,
embedding/runtime stack and live sandbox-account acceptance remain outstanding.
RAG/model-dependent and obsolete RAG HTTP suites remain outside default discovery.
The frontend still reports an approximately 666 kB uncompressed bundle warning.
Historical npm advisories are not a fresh dependency audit.

## Setup before use

Follow README for locked dependencies, operator password hash, exact allowed
origins, cookie settings, server AWS/model credentials and account pinning.
Terraform also requires the private runner token/configuration, immutable job
image selection, Engine 26+ and supported resource controls/subpath mounts.

Start **both API and worker**. Compose includes `job-worker`; for local development
run `python -m services.job_worker` in `backend` with the same environment/data
configuration as the API. Authenticated `/ready/jobs` reports heartbeat readiness;
`/ready/terraform` checks the runner. Without a worker, submitted jobs stay queued.

Before upgrading, stop all API/worker writers and back up application, Chroma,
runner ledger and workspace/state volumes. Preserve old root-owned/legacy data;
no actual volume ownership change or migration was performed. Do not delete volumes
or clear locks/runner ledger entries to force a retry.

## Remaining work and continuation

9. Repair filtered RAG retrieval/document replacement; evaluate retrieval; correct
   health scoring and cost comparison periods.
10. Broaden integration/end-to-end coverage, redaction/logging, operational recovery
    and controlled sandbox-account deployment validation.

Step 8 changes and checkpoint are local, uncommitted and unpushed. Pre-existing
PROJECT-ANALYSIS.md and project-handoff/ remain local and untracked.
Explain Step 9's current code/proposed changes/benefit and obtain approval before
implementation. **Do not start Step 9 automatically.** Implementation approval
is not permission for deployment or live infrastructure changes.

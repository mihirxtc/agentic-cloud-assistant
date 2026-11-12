# Agentic Cloud Assistant: project analysis

Reviewed 11 October 2026 against the local working tree. This is a source and limited local-test review, not confirmation of a deployed system. The existing handoff brief was used for orientation; findings below were checked against application source. No AWS operations, Terraform execution, model calls, or private configuration inspection were performed.

## Assessment

ACA is a substantial AWS operations prototype with a coherent scan → explain → prepare action → approve → execute workflow. Its strongest elements are the breadth of the dashboard, reusable MCP interface, deterministic security rules, and saved Terraform plan application. It is suitable for further development and controlled demonstrations. The current execution and identity boundaries are insufficient for a production service that holds cloud credentials.

The next investment should be trustworthy execution and accurate scan status rather than additional agent features. A narrower, dependable remediation workflow would offer more value than expanding the present tool surface.

## Architecture and product scope

The browser uses React 19, Vite, context-based state, and Recharts. A custom JSON-RPC MCP client calls the Python backend. FastAPI mounts FastMCP and also exposes generated `/tools/*` REST wrappers. Service modules handle boto3 inventory, seven security rules, AWS Cost Explorer reporting, Groq/Anthropic/Ollama model calls, Terraform subprocesses, and ChromaDB retrieval. Docker Compose places Nginx in front of the backend.

Core source entry points:

- `frontend/src/components/Dashboard.jsx`: panel orchestration and browser refresh.
- `frontend/src/api/mcpClient.js`: MCP initialization, session handling, requests, and SSE parsing.
- `backend/main.py`: Docker's HTTP entry point, MCP mount, REST wrappers, upload, and key downloads.
- `backend/mcp_server.py`: tool registration and application workflows; also a separate HTTP/stdio entry point.
- `backend/services/`: AWS, security, cost, model, and Terraform logic.
- `backend/rag/`: persistent knowledge collection, chunking, retrieval, and seed documents.

The dashboard covers infrastructure, security, costs, chat, Terraform, agent remediation, execution history, and knowledge documents. The security agent selects the first severity-sorted finding and prepares one action for approval. Durable multi-step orchestration and backend scheduled scanning are not implemented. Browser refresh and animated progress should not be mistaken for a background workflow engine.

Persistence consists of a JSON execution log, Terraform working directories/state, and embedded ChromaDB. There is no user database, tenant boundary, transactional job store, or migration system.

## Strengths

- AWS scanning, deterministic assessment, model explanations, and execution are separated into service modules. This gives useful seams for testing and later worker isolation.
- The same tools can serve the browser and MCP clients. This is a useful integration surface, provided every transport receives the same authorization controls.
- Terraform apply consumes the saved `tfplan`, preserving the planned resource changes rather than silently computing a new plan at approval time.
- Generated Terraform is validated, with one repair attempt using validation errors. This improves syntactic reliability, although validation is not a security policy check.
- Security rules are pure functions, with 28 directly executable tests. Inventory pagination, parallel IAM detail fetches, write locking for execution records, and source metadata in retrieval show attention to practical implementation details.

## Priority findings

### Critical: the login does not protect backend operations

Evidence: `frontend/src/contexts/AuthContext.jsx:6`, `backend/main.py:16`, `backend/mcp_server.py:413`, and `backend/mcp_server.py:761`.

Authentication is a hardcoded comparison in browser code and a localStorage flag. Backend tools, REST wrappers, key downloads, and document mutations have no application-level identity checks. Terraform approval is a boolean supplied by the caller; direct security-group revocation has no approval parameter. Anyone who can reach these interfaces can bypass the login UI and invoke available operations using the backend's authority or supplied credentials.

Implement server-validated identity and operation permissions across both MCP and REST. Bind executions and approvals to an authenticated actor, account, region, and saved plan hash. Enforce allowed job status transitions and prevent repeated approval of completed/rejected jobs.

### Critical: caller-provided Terraform executes in the backend environment

Evidence: `backend/mcp_server.py:372` and `backend/services/execution_service.py:59`.

The planning tool accepts arbitrary HCL, writes it to disk, and invokes Terraform with an inherited environment. There is no enforced provider/module/resource policy or isolated execution worker. HCL can invoke executable plugins and external data-source programs during planning; provisioners can run commands during apply. Consequently, the human approval step does not protect the planning phase from all execution risks. The image also runs as root.

Use isolated, disposable workers with restricted filesystem, network, and credentials. Enforce configuration policy before initialization or planning; use approved providers and retained lockfiles. Prefer structured, narrowly scoped remediation specifications over arbitrary HCL for security fixes.

References: [HashiCorp external data source](https://registry.terraform.io/providers/hashicorp/external/latest/docs/data-sources/external.html) and [provisioner behavior](https://developer.hashicorp.com/terraform/language/provisioners).

### High: failed scans can be reported as clean

Evidence: `backend/services/security_analyzer.py:377` and `backend/mcp_server.py:557`.

Rules return no findings for failed service scans. The aggregate returns a plain list, and the agent maps an empty list to `no_issues`. Direct local verification showed that an input where every service has `status: error` returns `[]`. This loses the distinction between successful inspection and absent evidence.

Return coverage and errors alongside findings. Treat incomplete scans as incomplete assessment; block remediation selection and clean-health claims when required evidence is missing. Preserve unknown states when permission-denied subcalls prevent inspection.

### High: SSH/RDP checks miss port ranges

Evidence: `backend/services/aws_scanner.py:330` and `backend/services/security_analyzer.py:31`.

The scanner stores only `FromPort` as `port`, discarding `ToPort`. SSH and RDP rules compare this value to 22 and 3389. An internet-facing TCP rule covering 0–65535 therefore escapes these checks. Direct verification of that normalized input returned no SSH finding.

Preserve protocol, start/end ports, and CIDRs. Detect whether the target port lies within the range, including all-protocol rules. Test IPv4, IPv6, ranges, exact ports, and scanner-to-analyzer behavior.

### High: scan and remediation can use different accounts

Evidence: `backend/mcp_server.py:94`, `frontend/src/components/Dashboard.jsx:45`, and `backend/services/execution_service.py:30`.

Dashboard inventory/security scans use server credentials. Remediation tools can receive browser credentials, and planning/applying can receive different credentials independently. There is no persisted STS account identity check tying inspected resources to the execution target. The settings and execution environment builders also lack an explicit session-token path for temporary credentials.

Use a single account connection abstraction, show the verified account and region, and check both before planning/applying. Prefer short-lived role credentials. AWS and model secrets currently persist in browser localStorage (`ApiKeyContext.jsx`), exposing them to any script executing within the application's origin.

### High: configured S3 state mode conflicts with the Docker Terraform version

Evidence: `backend/Dockerfile:9` and `backend/services/execution_service.py:338`.

The image installs Terraform 1.8.5, while generated S3 backend configuration sets `use_lockfile = true`. Native S3 state locking was introduced in Terraform 1.10, so setting `TF_STATE_BUCKET` makes this version combination incompatible. This is a source-confirmed mismatch; the image was not built or executed during review.

Align the pinned Terraform version with backend features and add a startup/version compatibility check. [Terraform 1.10 release notes](https://github.com/hashicorp/terraform/blob/v1.10.0/CHANGELOG.md).

### High: persistence mounts do not cover actual data paths

Evidence: `backend/rag/knowledge_base.py:11`, `backend/services/execution_service.py:15`, and `docker-compose.yml`.

Inside the container, Chroma's relative path resolves to `/chroma_db`, while Compose mounts `/app/chroma_db`. Execution history is written to `/app/execution_log.json`, which is not mounted. These datasets can survive a process restart but disappear when the container is replaced. Terraform working directories have a matching volume.

Configure explicit data paths under one mounted data root and test container replacement. Use transactional execution storage if concurrent workers or multiple replicas are required.

### High: execution identifiers lack base-directory containment

Evidence: `backend/services/execution_service.py:52`, `:273`, and `:371`.

Execution IDs are joined directly to `WORKDIR_BASE`. Key-file checks constrain the filename to the caller-selected workdir, but do not establish that the workdir is inside the base directory. Apply/destroy/cleanup tools also accept caller-supplied IDs. This creates a filesystem traversal boundary weakness; HTTP encoding/routing details determine exposure through the key-download route. No exploit was attempted.

Accept only the generated ID format, resolve paths under the base, reject escapes, and require a corresponding owned execution record before accessing artifacts.

## Workflow correctness and maintainability

- **Entry points diverge.** Docker runs `main:app`; Ollama HTTP status/pull routes are registered only in `mcp_server.create_app()`. Nginx proxies those routes, but Docker's app does not register them. Vite also omits `/api/ollama` from its development proxy. Consolidate route registration into one application factory.
- **Some proposed fixes do not remediate the issue.** The Terraform prompt recommends adding a restricted security-group rule while leaving the existing open rule for manual removal. Adding an allow rule does not revoke broader access. The agent's direct revocation path addresses this class separately, but general Terraform fixes require post-action verification.
- **Rollback is destruction.** The rollback tool invokes Terraform destroy; it does not restore an earlier configuration or undo direct SDK revocations. Name this operation accurately and capture inverse actions where restoration is supported.
- **Direct revocation can be broader than its description.** Matching a target port can revoke an entire range or all-protocol internet rule. Approval should show the exact permissions removed and resulting connectivity impact.
- **Cleanup can lose remote-state execution configuration.** Old-workdir purge protects local `terraform.tfstate`; S3-backed executions may have no local state and can lose their saved configuration if this purge runs.
- **RAG filters after retrieval.** Resource filtering is applied after global top-k retrieval, so relevant filtered documents outside the initial selection are missed. Pass filters into the vector query.
- **Document replacement leaves stale chunks.** Upsert replaces matching chunk IDs but does not remove old tail chunks when a document becomes shorter. Replace documents atomically or delete their previous chunks first.
- **Long-running work lacks a durable lifecycle.** Async paths contain synchronous scanning, SDK calls, or Terraform validation; some planning is correctly offloaded to a thread. Move expensive jobs to workers with persisted status, cancellation, and real progress reporting.
- **MCP client recovery is narrow.** SSE is read as a full response body; requests lack timeouts/cancellation, and recovery targets a specific 406 condition. Test session loss, malformed responses, server restarts, and slow operations.
- **Reproducibility is uneven.** The frontend has a lockfile, but most backend packages are unpinned. Terraform lockfiles are removed and a shared provider cache can be wiped during an execution retry, affecting concurrent work.

## Verification and limits

- All 21 tracked Python files passed AST syntax parsing.
- All 28 functions in `test_security_analyzer.py` passed by direct invocation. This was not a pytest run and does not exercise fixtures, plugins, or other suites.
- Targeted pure-function checks reproduced missed range-based SSH exposure and empty findings for failed scans.
- Pytest is unavailable in the current interpreter. Frontend dependencies are not installed, so build/lint and browser flows were not verified.
- RAG integration tests target legacy `/rag/documents`, `/rag/documents/text`, and `/rag/query` routes absent from the current FastAPI entry point. The benchmark likewise targets legacy routes and counts received error responses as successful timings.
- No frontend test suite or CI workflow was found in tracked source. Runtime imports, dependency compatibility, Docker builds, AWS behavior, model availability, and actual performance remain unverified.

## Recommended development sequence

1. Establish one authenticated backend boundary, account-bound connections, and owned execution records. Isolate Terraform execution and constrain accepted configurations.
2. Correct scan coverage/error semantics and port-range detection. Add focused regression tests for these reproduced failures.
3. Fix Terraform version compatibility, persistent data paths, and entry-point route drift. Verify Docker startup and container replacement using isolated test data.
4. Introduce durable execution status, approval transitions, account/region/plan binding, concurrency control, and post-action verification. Define supported restoration operations explicitly.
5. Repair API integration tests and benchmarks; add CI for backend tests and frontend build/lint. Restore a README with a canonical startup command, credential model, demo limits, and supported workflows.
6. Improve retrieval filtering/document replacement and model/job observability after the execution foundation is dependable.

For product validation, choose a specific operator and initial remediation scope. A focused AWS security assistant with reliable evidence and a few verified fixes is a clearer initial offering than a broad autonomous-cloud promise.

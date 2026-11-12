# Agentic Cloud Assistant — prototype handoff

Prepared from the repository on 9 October 2026. Repository HEAD at inspection: `78f88bf` (README file removed). This describes the working-tree source, not a deployed system. Model names and dependency versions below are values in the repository, not statements about current vendor availability.

## How to use this package

Upload this brief and `API-AND-FILE-INVENTORY.md` to ChatGPT. Paste the prompt in `CHATGPT-PROMPT.md`. If deeper implementation review is needed, also upload `SANITIZED-SOURCE-CONTEXT.txt`, or use the ZIP containing the tracked source and diagrams. The brief is the recommended starting point; source context is a reference, not something that must be read before every answer.

Scope: tracked application source, configuration templates, dependency manifests, tests, deployment configuration, and diagram filenames. Private `.env` files, AWS configuration, private keys, Terraform workdirs/state, execution records, vector database contents, Git internals, installed dependencies, and generated benchmarks are excluded. A hardcoded demo login password has been redacted in the source export. The archive retains diagram images as original reference artifacts; their contents were not independently checked against implementation. There is no evidence here about real account resources, deployment performance, users, revenue, or operating budget.

## 1. Product and current scope

The Agentic Cloud Assistant (ACA) is an AWS-focused prototype combining infrastructure visibility, rule-based security assessment, cost reporting, natural-language cloud chat, document retrieval, Terraform generation, and human-approved remediation. It supports a browser dashboard and MCP clients such as Claude Desktop through HTTP or stdio.

The intended user appears to be a cloud engineer or operator who wants to inspect an AWS account, understand findings in plain English, and generate or execute a fix. This is inferred from the code, not a validated customer definition. The repository does not specify whether production should be an internal tool, self-hosted product, single-tenant service, or multi-tenant SaaS.

It is not currently a general autonomous cloud platform. The security agent scans once, selects the first severity-sorted finding, prepares one action, and waits for approval. There is no durable multi-step agent orchestration, scheduled backend scanning, general AWS write-tool planner, or multi-cloud implementation.

## 2. Technology and dependencies

- Frontend: JavaScript/JSX, React and React DOM `^19.2.4`, Vite `^8.0.0`, React Vite plugin `^6.0.0`, Recharts `^3.8.1`, react-hot-toast `^2.6.0`, react-router-dom `^7.14.0`. The root app switches login/dashboard using context; a full routed application is not implemented. CSS files and inline styles provide the design system. A package lock exists.
- Frontend development: ESLint 9 with React hooks and refresh plugins; scripts are dev, build, lint, preview. No frontend test script or test suite was found.
- Backend: Python, FastAPI, Uvicorn, FastMCP, boto3, python-dotenv, httpx, python-dateutil, Anthropic SDK, Groq SDK, filelock, cachetools, PyYAML, ChromaDB, sentence-transformers, PyPDF2, python-multipart. Most Python dependencies are unpinned; pinned entries include ChromaDB 1.5.7, sentence-transformers 2.7.0, PyPDF2 3.0.1, python-multipart 0.0.9. No separate development dependency manifest is present.
- Infrastructure tooling: Terraform subprocesses, AWS/random/tls/local providers, optional S3 state backend. Provider constraints are AWS `~>5.0`, random `~>3.0`, TLS `~>4.0`, local `~>2.0`.
- Deployment: Docker Compose, Python 3.11 slim backend image, Node 20 Alpine frontend builder, Nginx runtime frontend. Backend Dockerfile installs Terraform 1.8.5.
- Persistence: JSON execution log, filesystem Terraform artifacts, ChromaDB embedded persistent database. No relational application database or migrations.

## 3. Architecture and boundaries

Browser → Nginx or Vite → MCP HTTP endpoint → registered tools in `backend/mcp_server.py` → service modules → AWS APIs, LLM providers, Terraform subprocesses, or ChromaDB.

`backend/main.py` constructs the FastAPI application, mounts the MCP HTTP app at `/mcp` with internal path `/`, shares its lifespan, installs local-development CORS, generates `/tools/*` REST wrappers and Swagger documentation, and adds health, PEM download, and multipart document-upload routes.

`backend/mcp_server.py` also exposes a separate Starlette HTTP application using `create_streamable_http_app`, with MCP and extra upload/Ollama routes. Running this file directly starts that application or stdio mode. Docker starts `main:app`. These entry points differ materially: Ollama status/pull routes are added by the standalone server factory but are not registered by `main.py`; health/docs/key routes belong to `main.py`. Do not assume either startup command gives the entire advertised interface.

The MCP client is custom frontend code. It initializes protocol version `2024-11-05`, sends the initialized notification, retains a session header, and calls tools through JSON-RPC POST. Responses may be JSON or SSE. Its SSE parser reads the complete response text and scans `data: ` lines rather than incrementally consuming events. It has limited reinitialization handling for a 406 failure. No frontend request timeout, cancellation system, comprehensive protocol negotiation, or durable session recovery is implemented.

`docs_generator.py` inspects FastMCP's private tool registry to build Pydantic request models and POST `/tools/{name}` handlers. Those handlers call tools through an in-process FastMCP client. This is executable API access, not static documentation. Primitive JSON schema types are mapped; full nested/union schema fidelity is limited. The private-registry dependency is a version-compatibility concern.

## 4. Implemented user interface

- Login: fixed demo username/password comparison in browser code. `aca_auth` localStorage flag determines login state. No server login, token validation, user store, roles, or password recovery.
- Settings: AWS region/access-key/secret-key fields; provider choice Groq, Anthropic, Ollama; API-key or Ollama-URL fields; save and clear controls. Values persist under `agentic_cloud_api_keys` in localStorage. No AWS session-token field.
- Dashboard: infrastructure, security, cost, chat, Terraform, security agent, execution history, and knowledge-base panels; top bar and settings modal.
- Infrastructure: manual scan, count tiles and resource-count chart; automatic browser refresh every 90 seconds after an initial scan; last refresh timestamp. This is not a backend scheduler or persistent inventory service.
- Security: severity counts and chart; severity-sorted expandable findings; Terraform fix prefill; direct fixes for SSH/RDP exposure; plain-English summary; health-score pill.
- Cost: current-month spend, month-over-month percentage, monthly trend area chart, service-spend bars, anomaly banner, LLM recommendations.
- Chat: sends natural-language questions with in-memory conversation history; displays replies. History is not stored server-side.
- Terraform: free-text requests and quick requests for EC2 t3.micro plus security group, versioned S3, public/private VPC subnets, and EC2 IAM role with S3 read access. Shows HCL, validation status/messages, resource type, description and naming note; copy and download HCL; run plan; approve/apply or reject; show outputs; download PEM files; download JSON report; re-scan; view history; destroy-based rollback.
- Security agent: run, step display, issue and summary, HCL/plan toggles, approve/reject, outputs, re-scan, JSON report, mark resolved, rollback, run again. Step advancement is timed every 1.2 seconds, not driven by backend progress events.
- Execution history: loads and reverses JSON-log records; expandable rows show timestamp, status, description, change counts, execution ID, plan/apply output.
- Knowledge base: question/answer interface with resource filter, sources, retrieved chunk count and relevance previews; list, add text, upload PDF/text via base64 MCP tool, delete documents.
- Appearance: accent color, panel radius, density, monospace font, security-score toggle; draggable tweaks panel and error boundary. The score toggle's wiring should be checked before treating it as a finished feature.

## 5. AWS inventory and security logic

EC2 scanner uses describe-instances pagination, includes pending/running/stopping/stopped instances, and returns ID, Name tag, type, state, public/private addresses, launch time, and security-group IDs.

S3 scanner lists buckets, checks bucket public-access-block settings, then public AllUsers ACL grants where ACLs are not ignored. Returns name, creation time, public flag and block-public-access flag. It does not fully evaluate bucket policies, account-level controls, access points, or all effective public access. Some denied checks become private-looking defaults.

IAM scanner paginates users and uses a thread pool to retrieve details: MFA devices, attached/inline policies, groups, access-key status/last use, creation time and password last used. Access-key IDs are abbreviated in output. Some subcalls swallow errors and supply defaults. PasswordLastUsed is treated as login/activity evidence, which does not establish whether a programmatic user is inactive.

Security-group scanner paginates groups and identifies IPv4 `0.0.0.0/0` and IPv6 `::/0` ingress. The normalized exposure stores FromPort as `port` but drops ToPort. SG usage scans ENIs, maps attached resources and labels inferred resource types EC2/ELB/RDS/Lambda/ECS/ENI. Empty attachment lists are reported as unused, although absence of ENIs alone does not prove safe deletion.

VPC scanner inventories VPCs, default status, CIDR, state, names and subnet counts. Existing-infrastructure context also lists resources/names to help HCL generation avoid conflicts, including IAM roles and S3 bucket names; exceptions can silently reduce context.

Seven deterministic rules are implemented:

- `SSH_PORT_OPEN`: HIGH; normalized exposure port equals 22.
- `RDP_PORT_OPEN`: HIGH; normalized exposure port equals 3389.
- `S3_BUCKET_PUBLIC`: HIGH; scanner public flag true.
- `UNRESTRICTED_ALL_TRAFFIC`: HIGH; internet ingress protocol is `-1`.
- `IAM_USER_NO_MFA`: MEDIUM; MFA false.
- `IAM_USER_INACTIVE`: LOW; no recorded console login or older than 90 days.
- `DEFAULT_VPC_IN_USE`: LOW; VPC is default, not proof of actual resource usage.

Findings contain `finding_id`, `resource_id`, `resource_type`, `rule`, `severity`, `title`, `description`, `recommendation`, and `metadata`. They are sorted HIGH → MEDIUM → LOW. IDs include list positions and are not a durable finding identity model.

Health score subtracts every finding's rule weight from a fixed total 16, clamps at zero and expresses a percentage. HIGH rule weights are 3, MFA 2, inactivity/default VPC 1. Repeated findings on many resources can exhaust the score; a failed scan can leave an empty list that appears healthy. This is a prototype score, not a compliance or exposure measure.

## 6. Costs and chat

Cost Explorer uses UnblendedCost, MONTHLY granularity, month-start to today (exclusive end), and a service breakdown. A first-day special case advances end by one day. Monthly trend starts N months before today at month-start and may include the current partial month. Anomaly logic flags ≥20% increase between the latest two entries; zero previous spend suppresses detection. Comparing partial current month to a full previous month is misleading without normalization. Cost Explorer is account-wide; tool region parameters are informational or unused.

Current-month errors return amount zero plus error, while some trend/service errors return empty lists. The summary tool still asks an LLM for recommendations, including only the top five services in its prompt. The frontend anomaly banner expects `service` and `note`, but the backend returns `message` and `percentage`.

AWS chat injects EC2/S3/IAM/SG/VPC, cost and SG-usage data. Its cache has 10 entries and five-minute TTL, keyed by region and first 12 characters of the server environment's access-key ID. This is not a tenant-scoped account identity. Scan cache is not explicitly invalidated after remediation. Browser chat does not pass selected AWS region, so default region can differ from dashboard selection. Ollama chat uses a compact inventory summary that omits the cost fields available to other providers.

## 7. LLM providers and prompts

Groq model configured: `llama-3.3-70b-versatile`. Anthropic model configured: `claude-haiku-4-5-20251001`. Ollama catalog: `gpt-oss:20b-cloud`, `gpt-oss:120b-cloud`, `qwen3-coder:480b-cloud`, `llama3.2:3b`; default is `gpt-oss:120b-cloud`. Some catalog prose recommends 20B even though the code defaults to 120B. Terraform's Ollama generation hardcodes 120B, while several other tool signatures accept an Ollama model name. The UI does not consistently expose/pass this model selection.

Chat and one-shot summaries use async httpx. Terraform Groq/Anthropic generation uses synchronous SDK calls inside async functions, which can block the event loop. Generic summary failures often return error text in normal answer strings, obscuring failure status. Summary responses generally use 1024-token limits; Terraform generation uses 4096. HTTP timeouts vary: typically 30 seconds for hosted summaries, 180 for Ollama. No centralized model registry, retry budget, token/cost accounting, eval suite, provider circuit breaker, or data-sharing policy.

Terraform Anthropic output is forced through a generate_terraform tool schema with HCL/resource_type/description. Groq/Ollama responses are parsed from fenced HCL and JSON blocks with regex. Generation runs terraform init/validate in a temporary directory; on invalid nonempty HCL it retries once with validation errors.

The system prompt requests random suffixes, default region us-east-1, a name_prefix, minimal resources, no inline backend configuration/credentials, provider constraints, and special EC2/RDS/VPC/IAM/storage patterns. EC2 generation requires TLS RSA-4096 keys, local PEM files with mode 0400, AWS key pairs, SSH ingress, and SSH command output. New EC2 SSH defaults to `0.0.0.0/0`; existing-group IP restrictions can default to a documentation-only IP placeholder. AMIs use an SSM parameter for Amazon Linux 2023 x86_64. Most AWS data sources are discouraged by prompts, with explicit SSM/IAM exceptions. AZs and network CIDRs are constructed from hardcoded patterns. These are instructions to a model, not enforced policies. They may be wrong for some account/region/architecture combinations and actively conflict with secure-by-default provisioning.

## 8. Terraform and remediation lifecycle

Execution IDs use timestamp plus eight UUID characters. Each execution gets `backend/terraform_workdirs/{execution_id}`. main.tf stores supplied HCL. Environment copies the backend process environment, optionally overwrites access key/secret/region, and sets a shared provider cache.

Plan: write HCL/backend config, remove provider lock file, init (120-second timeout), plan `-out=tfplan -no-color` (120-second timeout), parse text counts with regex, log plan result. Checksum failures can clear the shared plugin cache and retry. No isolated job sandbox, resource quotas, provider allowlist, policy evaluation, credential scoping, or per-execution lock.

Apply: apply saved binary plan with `-auto-approve`, 300-second timeout; collect `.pem` filenames on success; remove `.terraform` and tfplan after the subprocess returns; retain main.tf/state. Timeout/error paths can bypass cleanup. Failed applies can create resources partially; no reconciliation job exists. Saved binary plan is a good foundation for applying a reviewed plan, but there is no hash-bound approval, owner/account check, expiry, or transactional state transition.

Destroy: re-init saved main.tf with local or S3 backend and call `terraform destroy -auto-approve`. This is called rollback, but destroying created resources is not equivalent to restoring modified infrastructure or reversing direct AWS actions.

Remote state: if TF_STATE_BUCKET is set, backend.tf uses S3, execution-specific state key, encryption and `use_lockfile=true`. TF_STATE_REGION defaults us-east-1. The setup script and template still describe/create DynamoDB locking and TF_STATE_LOCK_TABLE, which execution code does not consume. Terraform 1.8.5 in Docker cannot support this generated native S3-lock configuration; native S3 locking arrived in 1.10. Reference: https://github.com/hashicorp/terraform/blob/v1.10.0/CHANGELOG.md.

Human plan summary parses counts, raises risk to high for destruction, and marks safe_to_approve if no destruction and risk not high. It cannot assess IAM escalation, data exposure, downtime or sensitive changes from counts alone.

Agent: scan all supported services → analyze → pick first finding → for SSH/RDP/all-traffic prepare direct SDK revocation; otherwise generate Terraform → plan → summarize → save pending execution → wait for separate approval. agent_run calls generate_terraform directly and does not use the RAG augmentation wrapper used by user-requested generation. It may lack existing-infrastructure context when using server credentials because that context scan is conditional on explicit credentials.

Direct revocation removes internet CIDRs from all rules covering the target port, including broad ranges and all-protocol rules. This may remove more access than a single-port description implies. A direct-fix MCP tool exists outside the agent approval flow. Direct actions do not capture an inverse permission set, so destroy-based rollback does not undo them.

Execution history uses a JSON array with FileLock for writes. Updates rewrite the whole file; reads are unlocked; corruption returns an empty list. It is not transactional, append-only, tamper-evident, tenant-scoped, or durable across replicas. Records may include HCL, issue, plan/apply/destroy output, change counts, SG action fields, approval timestamp, resolved markers. They do not record an authenticated actor, account binding or comprehensive artifact hashes.

PEM download restricts filename extension and separators, but execution_id itself lacks base-directory containment validation. Files remain on disk and downloads are not authenticated or one-time, despite UI wording about only retrieving once. Keys can also exist in Terraform state.

## 9. Knowledge base

ChromaDB collection `security_knowledge`, cosine distance, embedding model `all-MiniLM-L6-v2` (lazy-loaded encoder), word chunks of 400 with 50-word overlap. Chunk IDs are `{doc_id}__chunk_{i}`; metadata includes doc ID, index, total chunks, resource type and optional source/filename. Upsert updates overlapping chunk IDs but does not delete stale tail chunks when a replacement is shorter.

Retrieval defaults to top three, calculates relevance as 1 minus cosine distance, filters at 0.3, then applies resource filter after top-k selection. Post-filtering can miss relevant documents outside initial top-k. The conceptual tool description says max five, but this is not enforced uniformly at API boundaries. Source IDs are shown and retrieved text is inserted into prompts. No groundedness verification, citation-span checking, prompt-injection boundary, document ACLs, tenant segregation, version tracking, OCR, or ingestion job lifecycle.

Five seeded documents: aws-s3-security, aws-ec2-security, aws-iam-security, aws-vpc-security, terraform-security-patterns. These are bundled explanatory text, not automatically maintained authoritative AWS/CIS documents. Seed ingestion runs on import if collection empty. Broad initialization exceptions disable RAG silently. Tests use the live singleton collection rather than a consistently isolated test store.

PDF extraction uses PyPDF2 text extraction; plain text is decoded. Both base64 MCP upload and multipart upload paths exist. Multipart reads the entire file; no application-level size/page/chunk/embedding budget. Nginx limits body size to 50 MB, but direct backend access and memory/CPU consumption remain separate concerns. No original-document storage or malware scanning pipeline is implemented.

## 10. Deployment and configuration

Compose exposes Nginx on host port 80, backend internally on 8000, loads backend/.env, maps host.docker.internal for host Ollama, and waits for backend /health before frontend. Named volumes mount `/app/chroma_db` and `/app/terraform_workdirs`. Backend health is a simple uptime response, not dependency readiness. Restart policy is unless-stopped. Health check interval 30 seconds, timeout 10, three retries, 60-second startup period.

Nginx proxies MCP with buffering disabled and 300-second timeouts, upload, key routes, tools/docs/health, and Ollama routes with 600-second timeout. SPA fallback serves index.html. No TLS termination, identity gateway, request rate limits, or security headers are configured here.

Frontend Docker build sets VITE_MCP_URL=/mcp, while frontend development defaults to http://localhost:8000/mcp/. Some auxiliary requests use relative paths. Vite proxies common routes but not /api/ollama. Main FastAPI CORS allows localhost:5173 and exposes MCP session header. Standalone MCP app uses wildcard CORS including credentials.

Backend image downloads Terraform and preloads embedding model/providers, but runs as root and uses largely unpinned dependencies. Removing provider lockfiles undermines reproducible provider selection. No image vulnerability scan or release pipeline is in the repository. Runtime provider-cache behavior may invalidate baked providers or race concurrent jobs.

Chroma path resolves from `/app/rag/../../chroma_db` to `/chroma_db`, outside mounted `/app/chroma_db`. Execution log `/app/execution_log.json` is not mounted. Old-workdir purge protects only directories with local terraform.tfstate; an S3-backed applied directory can be deleted if this purge is invoked, losing local configuration needed for rollback. Purge runs only in the standalone main block, not Docker's main:app startup.

Template environment variables: GROQ_API_KEY, ANTHROPIC_API_KEY, OLLAMA_BASE_URL, AWS_DEFAULT_REGION, optional AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, TF_STATE_BUCKET, TF_STATE_REGION, legacy TF_STATE_LOCK_TABLE, DEBUG. DEBUG is documented but not wired to the FastAPI constructor. The S3 setup script has an incorrect ENV_FILE path relative to its location under backend/scipts. Its help/examples use a different script name. Do not run it blindly.

## 11. Tests, documentation and evidence

- 28 pure security-analyzer test functions were executed directly and passed during review. Pytest was unavailable, so this was not a full pytest run.
- Python syntax parsing passed in the initial inspection. This establishes syntax only, not import/dependency compatibility or runtime correctness.
- Ollama tests mock status and pull/auth behavior; RAG functional tests exercise chunking/retrieval/ingestion; integration tests expect a live server and legacy `/rag/documents`, `/rag/documents/text`, `/rag/query` REST routes that are absent from current main.py.
- Benchmark script targets legacy /scan, /security, /cost, /chat routes and collects only three timings by default. It counts completed HTTP responses as successful without validating HTTP success. It is not evidence of production performance.
- No frontend dependencies were installed, so build/lint/browser flows were not verified. No live AWS scans, plans, applies, destroys, model calls, image builds or dependency vulnerability scans were performed.
- No CI workflow, coverage gate, migration system, complete operational runbook, or current README was found. Fifteen PNG diagrams are tracked; the inventory lists them.

## 12. Production blockers and correctness gaps

Confirmed by source inspection; operational impact depends on deployment and configuration:

1. Browser-only hardcoded authentication; unauthenticated MCP, REST wrappers, document mutation, history, key downloads and cloud writes. Approval is a caller-provided boolean.
2. Arbitrary HCL runs in backend environment. Terraform can execute external programs during planning and provisioners during apply; provider initialization installs caller-selected plugins. Model prompt prohibitions are not enforcement. Reference: https://registry.terraform.io/providers/hashicorp/external/latest/docs/data-sources/external.html.
3. AWS keys and LLM keys persist in localStorage; no STS session-token interface, IAM-role onboarding or account-bound permissions model.
4. Dashboard scanning uses server credentials while remediation accepts browser credentials. No STS account identity displayed/checked across scan, plan and apply. Selected region does not consistently flow to chat/generation.
5. Failed or partial scans can appear clean; denied S3 checks can appear private; SSH/RDP rules miss port ranges and protocol distinctions; default VPC and IAM inactivity rules overstate what their evidence proves.
6. agent_run labels failed plans awaiting approval; agent_approve lacks execution-status enforcement and rejection artifact cleanup; replay/concurrency control missing. No atomic execution state machine or durable worker queue.
7. Unvalidated execution IDs affect plan/apply/destroy/cleanup paths. Filename validation does not validate the execution directory against WORKDIR_BASE.
8. Terraform/S3 version incompatibility; wrong Chroma volume path; nonpersistent execution log; unsafe remote-state workdir retention criteria.
9. UI marks rollback complete after any nonthrowing response, even rollback_failed; direct AWS rollback is unsupported. Verification checks global count, not target finding and account. Success messaging can exceed evidence.
10. Key-download UI implies one-time access without enforcement; EC2 generation actively requests internet SSH and creates state-stored private keys.
11. Caller-controlled Ollama URLs create a server-side network request surface requiring an explicit trust/allowlist model; model pull stream has no timeout and main.py lacks the advertised auxiliary routes.
12. RAG documents are shared, mutable and untrusted prompt content; no ACLs/versions; shorter replacement leaves stale chunks; filtering happens after retrieval; missing OCR and quotas.
13. Cost anomaly frontend/backend schema mismatch; partial-month comparisons; error-as-zero behavior; cost summaries may sound authoritative with missing data.
14. Sync SDK/subprocess/AWS work inside async functions can block requests; shared plugin cache changes are not coordinated; long operations remain tied to HTTP request lifetimes.
15. Brittle private FastMCP registry usage with unpinned version; sparse tests around real safety boundaries; legacy benchmark/integration API drift; no reproducible backend dependency lock or CI.

## 13. Features not currently implemented

The following are production design candidates, not present prototype capabilities: tenant/workspace/user models, OIDC/SSO/MFA and RBAC, account onboarding by cross-account AssumeRole, organization-wide multi-account/multi-region inventory, persistent snapshots, scheduled/event-driven scans, finding lifecycle/suppressions/ownership/SLAs, expanded AWS services and compliance controls, policy-as-code, deterministic remediation catalog, immutable approval/audit records, workflow workers and queues, job cancellation/recovery, drift detection, PR-based IaC changes, import/adoption of existing resources, cost budgets/forecasting/rightsizing, verified citations and RAG evaluations, secrets management, observability, alert integrations, backups/disaster recovery, accessibility/testing, release management, billing/entitlements, and multi-cloud.

No finite list can mean literally every production feature. Product audience, threat model, scale, budget, deployment mode and cloud scope must determine what belongs in the first release versus later phases.

## 14. Suggested discussion sequence for ChatGPT

Start by defining audience and deployment model. Then design secure account onboarding and a read-only production slice. Preserve useful deterministic analyzers and MCP integration while replacing authentication, account context, persistence and job execution. Add narrowly scoped, reviewed remediation playbooks with machine-enforced policy gates before permitting free-form generated HCL. Expand inventory/rules, cost analytics and RAG based on customer needs. Keep multi-cloud and elaborate autonomous loops as optional later work until AWS safety and operations are proven.

Ask for explicit acceptance criteria: denied scans never show healthy; every artifact binds tenant/account/region/actor; approval binds exact plan hash and expires; writes cannot bypass approval/RBAC; duplicate requests cannot double-apply; workers survive request disconnects; applied infrastructure remains recoverable after restarts; target-specific verification establishes the actual outcome; untrusted model/document content cannot authorize tools or access secrets.

The generated API inventory and sanitized source provide exact signatures and implementation context for assessing each proposal. Treat this document's recommendations as starting hypotheses rather than a preselected architecture.

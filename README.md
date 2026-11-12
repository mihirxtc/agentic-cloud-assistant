# Agentic Cloud Assistant

A web dashboard and MCP server for managing an AWS account with AI help.

- **Scan** AWS resources (EC2, S3, IAM, VPC, security groups).
- **Assess** security findings and get a check-based health score.
- **Report** costs: month to date and completed months.
- **Ask** questions answered from a security knowledge base (RAG).
- **Change** infrastructure with Terraform, only after you review and approve
  the plan.

The frontend is React (Vite). The backend is FastAPI + FastMCP. Terraform runs
in isolated Docker job containers, never on the host.

Architecture diagrams are in [aca-diagrams/](aca-diagrams/).

## How it works

```
Browser (React) ──► Nginx / Vite proxy ──► Backend API (FastAPI + MCP)
                                              │
                         ┌────────────────────┼─────────────────────┐
                         ▼                    ▼                     ▼
                    Job worker          SQLite + Chroma        AWS / LLMs
                         │
                         ▼
                 Terraform runner ──► isolated job container ──► AWS
```

- **Backend API** serves the dashboard, REST routes and MCP tools at `/mcp/`.
- **Job worker** runs long tasks (scans, assessments, plans, applies) in the
  background. The UI follows each job's progress.
- **Terraform runner** is the only service with Docker access. It runs each
  plan or apply in a locked-down container.
- **Storage**: SQLite for history, sessions and jobs; Chroma for the knowledge
  base.
- **LLMs**: Groq, Anthropic, or a local Ollama server.

## Requirements

- Python 3.11 and Node 22
- Docker Engine 26+ (for Docker Compose and the Terraform runner)
- AWS credentials available to the server (env vars, profile or IAM role)
- At least one LLM: Groq key, Anthropic key, or Ollama

## Quick start

### 1. Configure

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` to add your AWS and LLM settings. Never commit this file.

Create the login password hash (you will be prompted for the password):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python -m services.auth hash-password
```

Put the output in `ACA_OPERATOR_PASSWORD_HASH` and set `ACA_OPERATOR_USERNAME`.
There is no default password.

### 2a. Run with Docker (recommended)

```bash
cp backend/runner/config.example.json backend/runner/config.json
# Review resource_policy in config.json (EC2 needs allowed_amis).

docker compose --profile build build terraform-job
docker compose build terraform-runner
docker compose --profile setup run --rm runner-token-init
export ACA_JOB_IMAGE="$(docker image inspect aca-terraform-job:step7 --format '{{.Id}}')"
docker compose up --build
```

Open <http://localhost>.

### 2b. Run locally for development

Install `uv` and Node 22.12+ with npm, then run from the project root:

```bash
./scripts/setup.sh
./scripts/dev.sh
```

Setup installs dependencies and prompts for your login. Existing configuration is
preserved. Configure AWS/model access in `backend/.env` before using those tools.
The launcher starts the API, worker and frontend together at <http://localhost:5173>.
Use `./scripts/dev.sh --check` for preflight; Ctrl+C stops this run.

See [run_instructions.md](run_instructions.md) for the walkthrough and
[local launcher details](docs/operations.md#quick-local-start-linux) for options.
Terraform also requires the separate
[runner setup](docs/operations.md#initial-runner-setup).

## Using it with an MCP client

- **HTTP**: connect to `http://localhost:8000/mcp`. Create a token with
  `python -m services.auth generate-token`, put its digest in
  `ACA_MCP_TOKEN_SHA256`, and send `Authorization: Bearer <token>`.
- **stdio** (e.g. Claude Desktop): run `python mcp_server.py --stdio`.

See [claude_desktop_config_example.json](claude_desktop_config_example.json).

## Configuration

Main settings in `backend/.env`:

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL` | LLM providers |
| `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | AWS access (or use a profile/role) |
| `ACA_AWS_ACCOUNT_ID` | Optional: only allow this AWS account |
| `ACA_OPERATOR_USERNAME`, `ACA_OPERATOR_PASSWORD_HASH` | Dashboard login |
| `ACA_ALLOWED_ORIGINS` | Exact browser origins allowed to call the API |
| `ACA_COOKIE_SECURE` | `true` behind HTTPS, `false` for local HTTP |
| `ACA_MCP_TOKEN_SHA256` | Optional: bearer token digest for MCP clients |
| `TF_STATE_BUCKET`, `TF_STATE_REGION` | Optional: remote Terraform state in S3 |

Every option is documented in [backend/.env.example](backend/.env.example).

## Tests

```bash
cd backend && python -m pytest
cd frontend && npm run lint && npm test && npm run build
```

The tests run offline. They make no AWS or LLM calls. GitHub Actions runs them
on every push and pull request.

## Project layout

```
backend/
  main.py, http_app.py   FastAPI app (entry point: uvicorn main:app)
  mcp_server.py          MCP server (HTTP or --stdio)
  services/              AWS scanning, security, cost, auth, jobs, Terraform
  rag/                   Knowledge base and retrieval
  runner/                Isolated Terraform runner and job image
  tests/
frontend/
  src/                   React dashboard
aca-diagrams/            Architecture diagrams
docs/operations.md       Detailed operations and design reference
```

## Safety notes

- Changes to AWS only happen after you approve a reviewed plan.
- Failed or uncertain changes are never retried automatically.
- Before upgrading, stop the stack and back up all Docker volumes. Never use
  `docker compose down -v`.

## More documentation

[docs/operations.md](docs/operations.md) covers the details:

- [Storage, backups and upgrades](docs/operations.md#persistent-storage-and-upgrades)
- [Authentication and credentials](docs/operations.md#authentication-and-server-credentials)
- [AWS account and region binding](docs/operations.md#aws-account-and-region-binding)
- [Scan coverage limits](docs/operations.md#scan-coverage-and-assessment-limits)
- [Reviewed actions and destroy plans](docs/operations.md#reviewed-actions-retries-and-destroy-plans)
- [Isolated Terraform runner](docs/operations.md#isolated-terraform-runner)
- [Background jobs and recovery](docs/operations.md#background-jobs-progress-and-recovery-step-8)
- [Retrieval, scores and billing](docs/operations.md#retrieval-check-scores-and-billing-periods-step-9)

Project progress: [to_improve.md](to_improve.md).

# ACA — Agentic Cloud Assistant

**MSc Computing — Dissertation Project**

> An investigation into applying Large Language Models (LLMs) and the Model Context Protocol (MCP) to AWS cloud infrastructure management, automated security analysis, and natural-language Terraform generation.

---

## What Is This?

ACA is a web-based prototype built as part of an MSc Computing dissertation. It connects to your AWS account, scans your cloud infrastructure, identifies security problems, and uses AI (via Groq or Anthropic's Claude) to explain those problems in plain English and suggest fixes — including generating Terraform code to automate remediation.

The system has two parts:
- A **frontend** (the web interface you see in a browser), built with React
- A **backend** (the server that talks to AWS and the AI), built with Python

Both need to be running at the same time for the app to work.

![High Level System Architecture](aca-diagrams/HIGH_LEVEL_SYSTEM_ARCHITECTURE.png)

---

## Before You Begin — Prerequisites

You will need the following installed on your machine before proceeding. Each item links to an official download page.

| What | Why You Need It | How to Check |
|---|---|---|
| **Python 3.10 or newer** | Runs the backend server | `python3 --version` |
| **Node.js 18 or newer** | Runs the frontend dev server | `node --version` |
| **Terraform CLI (v1.x)** | Used to generate and apply infrastructure code | `terraform --version` |
| **Git** | To clone this repository | `git --version` |
| **An AWS Account** | The app connects to real AWS resources | — |
| **A Groq or Anthropic API key** | Powers the AI explanations and code generation | — |

> **Not sure if something is installed?** Open a terminal and run the command in the "How to Check" column above. If you see a version number, it is installed. If you see "command not found", you need to install it first.

### How to get a free Groq API key (recommended for development)

1. Go to [console.groq.com](https://console.groq.com) and sign up for a free account
2. Navigate to **API Keys** in the left sidebar
3. Click **Create API Key**, give it a name, and copy the key — you will need it later

---

## Step 1 — Download the Code

Open a terminal and run:

```bash
git clone <repository-url>
cd aca-app-dev
```

You should now be inside the project folder. You can confirm this by running `ls` — you should see folders named `backend` and `frontend`.

---

## Step 2 — Set Up the Backend (Python Server)

The backend is the engine of the application. It connects to AWS, runs security checks, and talks to the AI. Follow these steps carefully.

### 2.1 — Navigate into the backend folder

```bash
cd backend
```

### 2.2 — Create a virtual environment

A virtual environment is an isolated workspace for Python packages. This prevents the project's dependencies from conflicting with anything else on your machine.

```bash
python -m venv venv
```

This creates a new folder called `venv/` inside the backend directory. You only need to do this once.

### 2.3 — Activate the virtual environment

**On macOS / Linux:**
```bash
source venv/bin/activate
```

**On Windows:**
```bash
venv\Scripts\activate
```

> After activation, your terminal prompt should change to show `(venv)` at the beginning. This confirms the virtual environment is active. You must activate it every time you open a new terminal to run the backend.

### 2.4 — Install Python dependencies

```bash
pip install -r requirements.txt
```

This downloads and installs all the Python libraries the backend needs. It may take a few minutes on first run.

### 2.5 — Create the environment configuration file

The app uses a `.env` file to store sensitive keys (API keys, AWS credentials). A template is provided — copy it to create your own:

```bash
cp .env.example .env
```

### 2.6 — Open and edit the `.env` file

```bash
vi .env
```

> **New to `vi`?** Press `i` to enter insert mode, make your edits, then press `Esc`, type `:wq`, and press `Enter` to save and quit. Alternatively, you can open the file in any text editor (e.g., VS Code, Notepad, TextEdit).

At minimum, add your LLM API key. The file looks like this:

```
GROQ_API_KEY=your-groq-api-key-here
ANTHROPIC_API_KEY=                    # leave blank if using Groq
AWS_DEFAULT_REGION=us-east-1
```

A full list of available variables is in the [Environment Variables](#environment-variables) section below.

### 2.7 — Start the backend server

```bash
python3 main.py
```

You should see output like:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

The backend is now running. **Leave this terminal window open.** Open a new terminal window for the next step.

> You can also visit `http://localhost:8000/docs` in a browser to see an auto-generated interactive API reference for every tool the system exposes.

---

## Step 3 — Set Up the Frontend (Web Interface)

Open a **new terminal window** (keep the backend terminal running).

### 3.1 — Navigate to the frontend folder

From the project root:

```bash
cd frontend
```

### 3.2 — Install JavaScript dependencies

```bash
npm install
```

This downloads all the JavaScript libraries the interface needs. This may also take a few minutes on first run.

### 3.3 — Start the frontend development server

```bash
npm run dev
```

You should see output like:

```
  VITE v5.x.x  ready in 500ms

  ➜  Local:   http://localhost:5173/
```

The frontend is now running. Open `http://localhost:5173` in your browser.

---

## Step 4 — Using the Application

### 4.1 — Log in

When the app loads, you will be prompted to log in. Use these credentials:

- **Username:** `admin`
- **Password:** `demo2024`

> These are hardcoded development credentials for local use only. The app is not intended to be deployed publicly.

### 4.2 — Enter your AWS credentials

1. Click the **Settings** icon in the top-right corner
2. Go to the **Cloud Credentials** tab
3. Enter your **AWS Access Key ID**, **AWS Secret Access Key**, and **Region** (e.g., `us-east-1`)

> AWS credentials are held in memory only — they are never written to disk. If you restart the app, you will need to enter them again.

### 4.3 — Enter your LLM API key

1. Still in Settings, go to the **LLM Settings** tab
2. Paste your Groq or Anthropic API key
3. Select the model you want to use from the dropdown
4. Click **Save**

### 4.4 — Run a scan

Click the **Scan** button on the dashboard. The system will connect to AWS and retrieve information about your EC2 instances, S3 buckets, IAM users, Security Groups, and VPCs. This takes a few seconds.

### 4.5 — Analyse security findings

Once the scan is complete, click **Analyse** on the Security panel. The system will apply 7 built-in security rules to your infrastructure and generate an AI-written plain-English summary of any issues found.

---

## Environment Variables

All variables live in `backend/.env`. Copy from `backend/.env.example` to get started.

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | One of these two | Groq API key — used when model provider is set to Groq |
| `ANTHROPIC_API_KEY` | One of these two | Anthropic API key — used when model provider is set to Anthropic |
| `AWS_DEFAULT_REGION` | No | Default AWS region if not set in the UI (default: `us-east-1`) |
| `AWS_ACCESS_KEY_ID` | No | Server-side AWS key — alternative to entering credentials in the UI |
| `AWS_SECRET_ACCESS_KEY` | No | Server-side AWS secret |
| `TF_STATE_BUCKET` | No | S3 bucket for Terraform remote state storage (optional, see below) |
| `TF_STATE_LOCK_TABLE` | No | DynamoDB table for state locking (default: `terraform-state-lock`) |
| `TF_STATE_REGION` | No | Region for the S3 bucket and DynamoDB table (default: `us-east-1`) |
| `DEBUG` | No | Set to `true` to enable FastAPI auto-reload during development |

---

## Optional — Terraform S3 Remote State

By default, Terraform stores its state locally inside `backend/terraform_workdirs/`. If you want to explore durable, concurrent-safe remote state management (as discussed in the dissertation), you can set up an S3 backend with one command:

```bash
# From the project root
./setup_s3_backend.sh
```

This script will:
1. Create an S3 bucket with versioning and encryption enabled
2. Create a DynamoDB table for concurrent-apply locking
3. Automatically write the bucket name into `backend/.env`

To preview what the script will do without making any changes:

```bash
./setup_s3_backend.sh --dry-run
```

---

## Optional — Claude Desktop / Claude Code Integration

The same backend MCP server can be connected to Claude Desktop or Claude Code, allowing you to interact with your AWS infrastructure directly through an AI chat interface.

### Claude Code (HTTP transport)

Add the following as a streamable-HTTP MCP server in your Claude Code settings:

```
URL: http://localhost:8000/mcp
Type: streamable-http
```

### Claude Desktop (stdio transport)

Add the following to your Claude Desktop `claude_desktop_config.json` file. Replace the paths with the absolute paths on your machine:

```json
{
  "mcpServers": {
    "agentic-cloud-assistant": {
      "command": "/absolute/path/to/backend/venv/bin/python",
      "args": ["/absolute/path/to/backend/mcp_server.py", "--stdio"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/backend",
        "GROQ_API_KEY": "your-groq-api-key"
      }
    }
  }
}
```

See `claude_desktop_config_example.json` in the project root for a complete template.

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| `command not found: python3` | Python is not installed or not on PATH | Install Python 3.10+ from python.org |
| `command not found: npm` | Node.js is not installed | Install Node.js 18+ from nodejs.org |
| `command not found: terraform` | Terraform is not installed or not on PATH | Install from developer.hashicorp.com/terraform |
| Backend starts but frontend shows errors | Backend not running | Ensure `python3 main.py` is still running in the other terminal |
| `pip install` fails | Virtual environment not activated | Run `source venv/bin/activate` first |
| AWS scan returns no results | Credentials not entered or incorrect | Check Settings → Cloud Credentials in the UI |
| AI summary not generated | LLM API key missing or invalid | Check Settings → LLM Settings or verify `backend/.env` |

---

## Project Structure (Summary)

```
aca-app-dev/
├── backend/               # Python FastAPI + FastMCP server
│   ├── main.py            # Application entry point
│   ├── mcp_server.py      # All MCP tools (scanning, security, Terraform, RAG, chat)
│   ├── requirements.txt   # Python dependencies
│   ├── .env.example       # Environment variable template
│   └── services/          # AWS scanner, security engine, cost analyser, LLM wrappers
├── frontend/              # React web interface
│   ├── package.json       # JavaScript dependencies
│   └── src/               # UI components and panels
├── aca-diagrams/          # Architecture and sequence diagrams
└── setup_s3_backend.sh    # One-command S3 state backend setup
```

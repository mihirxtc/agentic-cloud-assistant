# Run the app locally

These steps are for Linux. You need `uv`, Node.js 22.12 or newer, npm, and internet access for the initial installation. Python 3.11 is selected automatically by `uv`.

## 1. Open the project

```bash
cd /home/mihirxtc/Projects/agentic-cloud-assistant
```

Run the following commands from this directory.

## 2. Run first-time setup

```bash
./scripts/setup.sh
```

This installs dependencies, creates `.venv` and `backend/.env` if missing, and prompts for your login username and password. Use a password of at least 12 characters. Existing configuration and configured passwords are preserved.

Installation includes PyTorch and can take several minutes. You do not need to run `python3.11` or activate `.venv` yourself.

## 3. Configure AWS and a model

Open the configuration file:

```bash
nano backend/.env
```

Keep these settings for local HTTP:

```dotenv
ACA_COOKIE_SECURE=false
ACA_ALLOWED_ORIGINS=http://localhost:5173
```

Keep the generated `ACA_OPERATOR_USERNAME` and `ACA_OPERATOR_PASSWORD_HASH` values.

Configure AWS using an existing server-side profile/role, or uncomment and fill in:

```dotenv
AWS_DEFAULT_REGION=eu-west-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
# Required when using temporary credentials:
# AWS_SESSION_TOKEN=your_session_token
# Optional: restrict the app to your account:
# ACA_AWS_ACCOUNT_ID=your_12_digit_account_id
```

Choose one model provider:

- **Groq:** set `GROQ_API_KEY` to your real key.
- **Anthropic:** set `ANTHROPIC_API_KEY` to your real key.
- **Local Ollama:** start Ollama with an installed model and use `OLLAMA_BASE_URL=http://localhost:11434`.

Leave unused cloud-model keys blank. Save the file and exit. Never commit `.env` or enter AWS keys in the browser.

## 4. Check and start

```bash
./scripts/dev.sh --check
./scripts/dev.sh
```

The launcher starts the API, background worker, and frontend in one terminal. Wait until it prints **Ready**. First startup may download the embedding model.

Open **http://localhost:5173** if the browser does not open automatically. Log in using the username and password from setup.

## 5. Try the app

1. Open Settings and select your region and configured model.
2. Verify the displayed AWS account and region.
3. Run an infrastructure scan, then a security assessment.
4. Use **Background Jobs** to view progress and reopen saved results.
5. Load costs or try a knowledge-base query.

Scans require valid AWS credentials and permissions. Terraform planning and applying additionally require the Docker runner setup in [operations guide](docs/operations.md#initial-runner-setup). The launcher does not configure that runner or grant approvals. The worker processes existing queued jobs, including previously approved actions.

## 6. Stop and restart

Press **Ctrl+C** in the launcher terminal. It allows active work to finish; press Ctrl+C again only if you need to force shutdown. Forced interruption may require reviewing job outcomes.

For later runs, you only need:

```bash
cd /home/mihirxtc/Projects/agentic-cloud-assistant
./scripts/dev.sh
```

Restart the launcher after changing backend code or `.env`. Rerun setup when dependencies change.

## Quick troubleshooting

- **Missing dependencies or login hash:** rerun `./scripts/setup.sh` interactively.
- **Port already occupied:** stop the conflicting service, or use `./scripts/dev.sh --api-port 8001`. For a different frontend port, add its exact origin to `.env` and run `./scripts/dev.sh --port 5174`.
- **No browser available:** use `./scripts/dev.sh --no-browser` and open the printed URL yourself.
- **Startup fails or takes too long:** inspect the log directory printed by the launcher under `.dev/logs/`. First-time model setup can take several minutes.
- **AWS verification fails:** check the server-side credentials, session expiry, region, account pin, and permissions.

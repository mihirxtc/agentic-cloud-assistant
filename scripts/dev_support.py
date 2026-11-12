"""Local-only setup/preflight helpers; never execute .env as shell code."""
import argparse
import getpass
from importlib import metadata
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]


def backend_imports():
    sys.path.insert(0, str(ROOT / 'backend'))
    from dotenv import dotenv_values, load_dotenv, set_key
    from services.auth import AuthConfig, password_hash, _parse_hash
    return dotenv_values, load_dotenv, set_key, AuthConfig, password_hash, _parse_hash


def configure(non_interactive=False):
    dotenv_values, _, set_key, _, password_hash, parse_hash = backend_imports()
    env_path = ROOT / 'backend/.env'
    created = False
    try:
        # Exclusive creation: never overwrite an existing operator configuration.
        with env_path.open('x', encoding='utf-8') as output:
            os.chmod(env_path, 0o600)
            template = (ROOT / 'backend/.env.example').read_text()
            template = template.replace('GROQ_API_KEY=your_groq_api_key_here', 'GROQ_API_KEY=')
            template = template.replace('ANTHROPIC_API_KEY=your_anthropic_api_key_here', 'ANTHROPIC_API_KEY=')
            template = template.replace('OLLAMA_BASE_URL=http://host.docker.internal:11434',
                                        'OLLAMA_BASE_URL=http://localhost:11434')
            output.write(template)
            output.flush()
            os.fsync(output.fileno())
            created = True
    except FileExistsError:
        pass
    values = dotenv_values(env_path, interpolate=False)
    encoded = values.get('ACA_OPERATOR_PASSWORD_HASH') or ''
    if encoded:
        # A deliberately interpolated/environment-supplied hash is left untouched.
        if not encoded.startswith('${'):
            parse_hash(encoded)
        print('Existing login configuration preserved.')
        return
    if non_interactive:
        print('Configuration prepared; browser login still needs a password hash. Run setup.sh interactively or configure backend/.env.')
        return
    if not sys.stdin.isatty():
        raise ValueError('Login needs an interactive terminal. Use --non-interactive to install without prompting, then configure backend/.env.')
    username = (values.get('ACA_OPERATOR_USERNAME') or '').strip()
    if created or not username:
        username = input(f'Operator username [{username or "admin"}]: ').strip() or username or 'admin'
        if not 1 <= len(username) <= 128:
            raise ValueError('Username must contain 1–128 characters')
    password = getpass.getpass('Choose a local login password (at least 12 characters): ')
    confirmation = getpass.getpass('Repeat password: ')
    if password != confirmation:
        raise ValueError('Passwords do not match; no login values were changed')
    encoded = password_hash(password)
    set_key(env_path, 'ACA_OPERATOR_USERNAME', username)
    set_key(env_path, 'ACA_OPERATOR_PASSWORD_HASH', encoded)
    print('Local login configured; only the salted password hash was saved.')


def check(api_port, web_port):
    if sys.version_info[:2] != (3, 11):
        raise ValueError('The project .venv must use Python 3.11. Run scripts/setup.sh.')
    required = ('uvicorn', 'fastapi', 'fastmcp', 'chromadb', 'sentence-transformers',
                'torch', 'boto3', 'filelock', 'python-dotenv')
    missing = []
    for package in required:
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            missing.append(package)
    if missing:
        raise ValueError('Missing backend dependencies: ' + ', '.join(missing) + '. Run ./scripts/setup.sh.')
    for package in ('vite', 'react'):
        if not (ROOT / 'frontend/node_modules' / package / 'package.json').is_file():
            raise ValueError('Frontend dependencies are missing. Run ./scripts/setup.sh.')
    env_path = ROOT / 'backend/.env'
    if not env_path.is_file():
        raise ValueError('backend/.env is missing. Run ./scripts/setup.sh.')
    _, load_dotenv, _, AuthConfig, _, _ = backend_imports()
    load_dotenv(env_path, override=False)
    try:
        config = AuthConfig.from_env()
    except ValueError:
        raise ValueError('Invalid authentication settings in the environment/backend/.env. Check username, password hash, cookie, origins and session duration.') from None
    if not config.password_hash:
        raise ValueError('Browser login has no password hash. Run ./scripts/setup.sh interactively.')
    if config.secure_cookie:
        raise ValueError('This launcher serves local HTTP. Set ACA_COOKIE_SECURE=false in backend/.env for local development.')
    origin = f'http://localhost:{web_port}'
    if origin not in config.origins:
        raise ValueError(f'Add {origin} to ACA_ALLOWED_ORIGINS in backend/.env. Existing origins were not changed.')
    probes = []
    try:
        for name, port in (('API', api_port), ('frontend', web_port)):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probes.append(probe)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise ValueError(f'{name} port {port} is unavailable. Stop the existing process or choose another port.') from None
    finally:
        for probe in probes:
            probe.close()
    print('Preflight passed: Python, dependencies, local login, browser origin and ports.')
    print('AWS/model access is checked by the app; no cloud requests were made.')


def worker_ready():
    # Query only local readiness metadata, without importing the tool registry,
    # loading models, calling AWS, or provisioning a bearer token.
    try:
        _, load_dotenv, _, _, _, _ = backend_imports()
        load_dotenv(ROOT / 'backend/.env', override=False)
        from services.jobs import Jobs
        return Jobs().worker_ready()
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    config_parser = commands.add_parser('configure')
    config_parser.add_argument('--non-interactive', action='store_true')
    check_parser = commands.add_parser('check')
    check_parser.add_argument('--api-port', type=int, required=True)
    check_parser.add_argument('--port', type=int, required=True)
    commands.add_parser('worker-ready')
    args = parser.parse_args()
    try:
        if args.command == 'configure':
            configure(args.non_interactive)
        elif args.command == 'check':
            check(args.api_port, args.port)
        else:
            return 0 if worker_ready() else 1
    except (ValueError, OSError, ImportError):
        # No traceback: configuration/library errors can contain secret inputs.
        if args.command == 'worker-ready':
            return 1
        # These helpers raise only fixed, secret-free ValueError messages.
        error = sys.exception()
        print(str(error) if isinstance(error, ValueError) else 'Setup/preflight failed. Check dependencies and access to backend/.env.', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSetup cancelled; rerun when ready.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())

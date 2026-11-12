"""Launcher contract checks: fake services, real process groups and loopback HTTP."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
REAL_PYTHON = sys.executable

FAKE_SERVICE = r'''
import json, os, signal, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
state = Path(os.environ['FAKE_STATE'])
args = sys.argv[1:]
if args and args[0].endswith('dev_support.py'):
    if args[1] == 'worker-ready':
        sys.exit(0 if (state/'worker.ready').exists() else 1)
    sys.exit(0)
role = 'frontend' if args[0] == 'frontend' else 'api' if 'uvicorn' in args else 'worker'
with (state/'pids.jsonl').open('a') as output:
    output.write(json.dumps({'role':role, 'pid':os.getpid()})+'\n')
if role == 'worker':
    if os.environ.get('FAKE_FAIL_WORKER'): sys.exit(9)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    with (state/'pids.jsonl').open('a') as output:
        output.write(json.dumps({'role':'descendant', 'pid':child.pid})+'\n')
    (state/'worker.ready').touch()
    def stop(*_):
        child.wait(timeout=3)
        if os.environ.get('FAKE_STUBBORN_WORKER'):
            while True: time.sleep(.1)
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    while True: time.sleep(.1)
if role == 'frontend' and os.environ.get('FAKE_FAIL_FRONTEND'): sys.exit(13)
port = int(args[args.index('--port')+1])
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.send_response(503 if role == 'api' and os.environ.get('FAKE_NOT_READY') else 200)
        self.end_headers()
        self.wfile.write(b'{}')
server = HTTPServer(('127.0.0.1', port), Handler)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
if role == 'frontend' and os.environ.get('FAKE_EXIT_AFTER_READY'):
    signal.signal(signal.SIGALRM, lambda *_: sys.exit(17))
    signal.alarm(2)
try: server.serve_forever()
finally: server.server_close()
'''


def free_ports():
    probes = [socket.socket(), socket.socket()]
    try:
        for probe in probes:
            probe.bind(('127.0.0.1', 0))
        return [probe.getsockname()[1] for probe in probes]
    finally:
        for probe in probes:
            probe.close()


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='aca-launcher-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project with spaces'
        for name in ('scripts', 'backend', 'frontend', '.venv/bin', 'fakebin', 'state'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / 'scripts/dev.sh').write_text((SCRIPTS/'dev.sh').read_text())
        (self.root / 'fake.py').write_text(FAKE_SERVICE)
        for name, args in (('.venv/bin/python', ''), ('fakebin/npm', 'frontend')):
            target = self.root / name
            target.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(REAL_PYTHON) + ' ' + shlex.quote(str(self.root/'fake.py')) + ' ' + args + ' "$@"\n')
            target.chmod(0o755)
        node = self.root / 'fakebin/node'
        node.write_text('#!/usr/bin/env bash\nexit 0\n'); node.chmod(0o755)
        self.api, self.web = free_ports()
        self.env = {**os.environ, 'PATH': str(self.root/'fakebin') + os.pathsep + os.environ['PATH'],
                    'FAKE_STATE': str(self.root/'state'), 'ACA_DEV_STARTUP_TIMEOUT': '3', 'ACA_DEV_SHUTDOWN_TIMEOUT': '3'}
        self.processes = []
        self.addCleanup(self.stop_all)

    def start(self, *extra, **env):
        output = self.root / ('output-' + str(len(self.processes)))
        with output.open('w') as log:
            process = subprocess.Popen(['bash', str(self.root/'scripts/dev.sh'), '--no-browser',
                '--api-port', str(self.api), '--port', str(self.web), *extra], cwd='/',
                env={**self.env, **env}, stdout=log, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process, output

    def records(self):
        target = self.root/'state/pids.jsonl'
        return [json.loads(line) for line in target.read_text().splitlines()] if target.exists() else []

    def stop_all(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=6)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        for record in self.records():
            if alive(record['pid']):
                try: os.kill(record['pid'], signal.SIGKILL)
                except ProcessLookupError: pass

    def wait_text(self, process, output, text):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if text in output.read_text(): return
            if process.poll() is not None: break
            time.sleep(.05)
        self.fail(output.read_text())

    def assert_children_gone(self):
        for record in self.records():
            self.assertFalse(alive(record['pid']), record)

    def test_start_signal_cleanup_and_unrelated_process_survives(self):
        unrelated = subprocess.Popen([REAL_PYTHON, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait()))
        process, output = self.start()
        self.wait_text(process, output, 'Ready: http://localhost:')
        self.assertEqual({r['role'] for r in self.records()}, {'api', 'worker', 'frontend', 'descendant'})
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=6), 130, output.read_text())
        self.assert_children_gone()
        self.assertIsNone(unrelated.poll())

    def test_shutdown_deadline_forces_only_owned_processes(self):
        process, output = self.start(FAKE_STUBBORN_WORKER='1', ACA_DEV_SHUTDOWN_TIMEOUT='1')
        self.wait_text(process, output, 'Ready: http://localhost:')
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=6), 130, output.read_text())
        self.assertIn('Forcing this run to stop', output.read_text())
        self.assert_children_gone()

    def test_second_interrupt_forces_shutdown(self):
        process, output = self.start(FAKE_STUBBORN_WORKER='1', ACA_DEV_SHUTDOWN_TIMEOUT='10')
        self.wait_text(process, output, 'Ready: http://localhost:')
        process.send_signal(signal.SIGINT)
        self.wait_text(process, output, 'Stopping this run;')
        time.sleep(.2)  # Allow the worker to reap its already-terminated child.
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=5), 130, output.read_text())
        self.assertIn('Forcing this run to stop', output.read_text())
        self.assert_children_gone()

    def test_worker_start_failure_cleans_api(self):
        process, output = self.start(FAKE_FAIL_WORKER='1')
        self.assertNotEqual(process.wait(timeout=8), 0)
        self.assertIn('service exited', output.read_text())
        self.assert_children_gone()

    def test_frontend_start_failure_cleans_worker_and_descendant(self):
        process, output = self.start(FAKE_FAIL_FRONTEND='1')
        self.assertNotEqual(process.wait(timeout=8), 0)
        self.assert_children_gone()

    def test_unexpected_exit_after_readiness_stops_remaining_services(self):
        process, output = self.start(FAKE_EXIT_AFTER_READY='1')
        self.wait_text(process, output, 'Ready: http://localhost:')
        self.assertNotEqual(process.wait(timeout=8), 0)
        self.assert_children_gone()

    def test_readiness_timeout_cleans_partial_startup(self):
        process, output = self.start(FAKE_NOT_READY='1', ACA_DEV_STARTUP_TIMEOUT='1')
        self.assertNotEqual(process.wait(timeout=6), 0)
        self.assertIn('Startup timed out', output.read_text())
        self.assert_children_gone()

    def test_second_launcher_cannot_own_same_project(self):
        first, output = self.start()
        self.wait_text(first, output, 'Ready: http://localhost:')
        second, second_output = self.start()
        self.assertNotEqual(second.wait(timeout=4), 0)
        self.assertIn('Another dev.sh run', second_output.read_text())
        self.assertIsNone(first.poll())

    def test_check_only_starts_no_services(self):
        process, output = self.start('--check')
        self.assertEqual(process.wait(timeout=4), 0, output.read_text())
        self.assertEqual(self.records(), [])
        self.assertFalse((self.root/'.dev').exists())


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('dev_support', SCRIPTS/'dev_support.py')
        self.support = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.support)
        self.temporary = tempfile.TemporaryDirectory(prefix='aca-setup-test-')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root/'backend').mkdir()
        (root/'backend/.env.example').write_text((SCRIPTS.parent/'backend/.env.example').read_text())
        self.env_path = root/'backend/.env'
        # Keep application module imports against the real source; all writes go to the fixture.
        imports = self.support.backend_imports()
        self.support.ROOT = root
        self.support.backend_imports = lambda: imports
        self.output = io.StringIO()

    def test_noninteractive_creates_private_template_without_fake_keys(self):
        with redirect_stdout(self.output): self.support.configure(non_interactive=True)
        text = self.env_path.read_text()
        self.assertNotIn('your_groq_api_key_here', text)
        self.assertNotIn('your_anthropic_api_key_here', text)
        self.assertIn('OLLAMA_BASE_URL=http://localhost:11434', text)
        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)
        self.assertIn('still needs a password hash', self.output.getvalue())

    def test_existing_configuration_is_byte_identical(self):
        password_hash = self.support.backend_imports()[4]
        original = '# Keep my custom values\nGROQ_API_KEY=existing-secret\nACA_OPERATOR_PASSWORD_HASH=' + password_hash('long-test-password') + '\n'
        self.env_path.write_text(original)
        with redirect_stdout(self.output): self.support.configure()
        self.assertEqual(self.env_path.read_text(), original)
        self.assertNotIn('existing-secret', self.output.getvalue())

    def test_interactive_password_is_hashed_without_printing(self):
        with patch.object(self.support.sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', return_value='tester'), \
             patch.object(self.support.getpass, 'getpass', return_value='long-test-password'), \
             redirect_stdout(self.output):
            self.support.configure()
        text = self.env_path.read_text()
        self.assertIn('pbkdf2_sha256:', text)
        self.assertNotIn('long-test-password', text + self.output.getvalue())
        self.assertIn("ACA_OPERATOR_USERNAME='tester'", text)


if __name__ == '__main__':
    unittest.main()

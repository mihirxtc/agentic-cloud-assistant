"""Operator/CI smoke test. Creates only randomly named dummy Docker resources; no AWS."""
import argparse
import json
import subprocess
import tempfile
import re
import uuid
from runner.broker import Broker

IDENTIFIER = 'exec_20000101_000000_00000000'
PROBE = '''import json, os, socket
from pathlib import Path
assert os.getuid() == 10001
cgroup = Path('/sys/fs/cgroup')
if (cgroup/'memory.max').exists():
    memory = int((cgroup/'memory.max').read_text())
    pids = int((cgroup/'pids.max').read_text())
    quota, period = map(int, (cgroup/'cpu.max').read_text().split())
else:
    memory = int((cgroup/'memory/memory.limit_in_bytes').read_text())
    pids = int((cgroup/'pids/pids.max').read_text())
    quota = int((cgroup/'cpu/cpu.cfs_quota_us').read_text())
    period = int((cgroup/'cpu/cpu.cfs_period_us').read_text())
assert 0 < memory <= 768 * 1024 * 1024
assert 0 < pids <= 64
assert 0 < quota <= period
assert not os.path.exists('/var/run/docker.sock')
assert not os.path.exists('/app') and not os.path.exists('/control')
assert not os.path.exists('/workspace/sibling.txt')
assert os.path.isfile('/workspace/sentinel.txt')
assert not any(k in os.environ for k in ('AWS_SECRET_ACCESS_KEY','GROQ_API_KEY','ACA_OPERATOR_PASSWORD_HASH','ACA_MCP_TOKEN_SHA256'))
try:
    open('/forbidden-write', 'w').write('bad')
except OSError:
    pass
else:
    raise AssertionError('Root filesystem is writable')
try:
    socket.create_connection(('1.1.1.1',443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('Direct internet routing is available')
try:
    socket.getaddrinfo('example.com', 443)
except OSError:
    pass
else:
    raise AssertionError('External DNS bypass is available')
open('/workspace/probe.txt', 'w').write('isolated')
print(json.dumps({'uid':os.getuid(), 'isolated':True}))
'''


def command(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=90)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True, help='Immutable sha256 image ID built from Dockerfile.job')
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', args.image):
        raise SystemExit('Supply the immutable locally built job image ID')
    suffix = uuid.uuid4().hex
    volume, network, name = 'aca-smoke-' + suffix, 'aca-smoke-net-' + suffix, 'aca-smoke-job-' + suffix
    try:
        command(['docker', 'volume', 'create', volume])
        command(['docker', 'network', 'create', '--internal', network])
        # This explicit initialization helper only owns the new dummy volume.
        seed = f"import os; os.mkdir('/seed/{IDENTIFIER}',0o700); os.chown('/seed/{IDENTIFIER}',10001,10001); open('/seed/{IDENTIFIER}/sentinel.txt','w').write('own'); open('/seed/sibling.txt','w').write('other')"
        command(['docker', 'run', '--rm', '--network=none', '--user=0:0', '--entrypoint=python',
                 '--mount', 'type=volume,source=' + volume + ',target=/seed', args.image, '-c', seed])
        with tempfile.TemporaryDirectory() as temporary:
            broker = Broker(temporary + '/workspaces', temporary + '/control', args.image, volume, network, {})
            run = broker.command(IDENTIFIER, name)
            # Test entrypoint override is restricted to this operator-only smoke tool.
            run[-1:] = ['--entrypoint=python', args.image, '-c', PROBE]
            result = command(run)
            assert json.loads(result.stdout) == {'uid': 10001, 'isolated': True}
        print('Dummy container isolation smoke test passed; no AWS credentials/resources used.')
    finally:
        for cleanup in (['docker', 'rm', '-f', name], ['docker', 'network', 'rm', network], ['docker', 'volume', 'rm', volume]):
            subprocess.run(cleanup, capture_output=True, timeout=30, check=False)


if __name__ == '__main__':
    main()

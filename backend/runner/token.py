"""Explicit operator bootstrap. Never prints the secret or overwrites one implicitly."""
import os
from pathlib import Path
import secrets
import sys
from runner.broker import token_from_file


TOKEN_PATH = Path('/credentials/token')


def main():
    path = TOKEN_PATH
    if sys.argv[1:] == ['--rotate']:
        path.unlink(missing_ok=True)
    elif sys.argv[1:]:
        raise SystemExit('Use no arguments to initialize, or --rotate after stopping clients')
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    except FileExistsError:
        token_from_file(path)
        print('Existing runner token retained.')
        return
    with os.fdopen(descriptor, 'w') as output:
        output.write(secrets.token_hex(32)); output.flush(); os.fsync(output.fileno())
    print('Runner token initialized in the private credential volume; value was not displayed.')


if __name__ == '__main__':
    main()

"""Build-time installation of checked release archives for amd64/arm64 only."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import tempfile
from urllib.request import urlopen
import zipfile


def main():
    versions = json.loads(Path(__file__).with_name('versions.json').read_text())
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if arch is None:
        raise SystemExit('Unsupported architecture')
    for name, settings in versions.items():
        product = 'terraform' if name == 'terraform' else 'terraform-provider-' + name
        archive = f"{product}_{settings['version']}_linux_{arch}.zip"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / archive
            with urlopen(f"https://releases.hashicorp.com/{product}/{settings['version']}/{archive}", timeout=60) as response, path.open('wb') as output:
                shutil.copyfileobj(response, output)
            with path.open('rb') as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
            if actual != settings[arch]:
                raise SystemExit('Release checksum mismatch')
            target = Path('/usr/local/bin') if name == 'terraform' else Path(f"/providers/registry.terraform.io/hashicorp/{name}/{settings['version']}/linux_{arch}")
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(path) as package:
                for member in package.infolist():
                    if member.filename in ('.', '..') or '/' in member.filename or '\\' in member.filename or member.is_dir():
                        raise SystemExit('Unsafe release archive member')
                    destination = target / member.filename
                    with package.open(member) as source, destination.open('wb') as output:
                        shutil.copyfileobj(source, output)
                    destination.chmod(0o755 if member.filename.startswith('terraform') else 0o644)
    Path('/opt/aca').mkdir(parents=True, exist_ok=True)
    cli, config = build_configuration(versions)
    Path('/opt/aca/terraform.rc').write_text(cli)
    Path('/build').mkdir()
    Path('/build/main.tf').write_text(config)


def build_configuration(versions):
    cli = 'provider_installation {\n filesystem_mirror {\n path = "/providers"\n include = ["registry.terraform.io/hashicorp/*"]\n }\n}\ndisable_checkpoint = true\n'
    config = 'terraform {\nrequired_providers {\n' + '\n'.join(
        f'{name} = {{ source = "hashicorp/{name}", version = "= {settings["version"]}" }}'
        for name, settings in versions.items() if name != 'terraform') + '\n}\n}\n'
    return cli, config



if __name__ == '__main__':
    main()

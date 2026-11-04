"""Fail-closed v1 policy for a deliberately small Terraform resource surface."""
import ipaddress
import json
from pathlib import Path
import re
from types import SimpleNamespace

from services.aws_terraform_guard import validate_aws_configuration

VERSIONS = json.loads(Path(__file__).with_name('versions.json').read_text())
POLICY_VERSION = 'aca-terraform-v1'
# Unsupported resources/arguments fail before providers execute.
FIELDS = {
    'aws_s3_bucket': {'bucket', 'tags', 'force_destroy'},
    'aws_s3_bucket_public_access_block': {'bucket', 'block_public_acls', 'ignore_public_acls', 'block_public_policy', 'restrict_public_buckets'},
    'aws_s3_bucket_versioning': {'bucket', 'versioning_configuration'},
    'aws_s3_bucket_server_side_encryption_configuration': {'bucket', 'rule'},
    'aws_vpc': {'cidr_block', 'enable_dns_support', 'enable_dns_hostnames', 'tags'},
    'aws_subnet': {'vpc_id', 'cidr_block', 'availability_zone', 'map_public_ip_on_launch', 'tags'},
    'aws_security_group': {'name', 'description', 'vpc_id', 'ingress', 'egress', 'tags'},
    'aws_instance': {'ami', 'instance_type', 'subnet_id', 'vpc_security_group_ids', 'key_name', 'associate_public_ip_address', 'root_block_device', 'metadata_options', 'tags'},
    'aws_key_pair': {'key_name', 'public_key', 'tags'},
    'tls_private_key': {'algorithm', 'rsa_bits'},
    'random_id': {'byte_length', 'prefix'},
}
TAGGABLE = {'aws_s3_bucket', 'aws_vpc', 'aws_subnet', 'aws_security_group', 'aws_instance', 'aws_key_pair'}
TARGET_FIELDS = {'bucket': 'aws_s3_bucket', 'vpc_id': 'aws_vpc', 'subnet_id': 'aws_subnet',
                 'vpc_security_group_ids': 'aws_security_group', 'key_name': 'aws_key_pair'}
FUNCTIONS = {'format', 'join', 'toset', 'tolist', 'tomap', 'length', 'concat', 'lookup', 'coalesce', 'try', 'can',
             'tostring', 'cidrsubnet', 'merge', 'replace', 'substr', 'lower', 'upper', 'split', 'keys', 'values', 'sort', 'jsonencode'}


def policy_config(value=None):
    value = value or {}
    if not isinstance(value, dict) or set(value) - {'authorized_targets', 'allowed_amis', 'admin_cidrs'}:
        raise ValueError('Invalid server resource policy')
    result = {'authorized_targets': value.get('authorized_targets', {}), 'allowed_amis': value.get('allowed_amis', []), 'admin_cidrs': value.get('admin_cidrs', [])}
    if not isinstance(result['authorized_targets'], dict) or set(result['authorized_targets']) - set(TARGET_FIELDS.values()):
        raise ValueError('Invalid target policy')
    for values in result['authorized_targets'].values():
        if not isinstance(values, list) or not all(isinstance(item, str) and len(item) < 128 for item in values):
            raise ValueError('Invalid authorized target IDs')
    if not isinstance(result['allowed_amis'], list) or not all(re.fullmatch(r'ami-[a-f0-9]{8,17}', str(value)) for value in result['allowed_amis']):
        raise ValueError('Only explicitly approved AMI IDs are permitted')
    if not isinstance(result['admin_cidrs'], list):
        raise ValueError('Invalid admin CIDRs')
    for cidr in result['admin_cidrs']:
        if ipaddress.ip_network(cidr).prefixlen == 0:
            raise ValueError('World-open administrative access is not permitted')
    return result


def resources(config):
    result = {}
    for block in config.get('resource', []):
        for kind, entries in block.items():
            for name, settings in entries.items():
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                    raise ValueError('Unsupported resource label')
                address = kind + '.' + name
                if address in result:
                    raise ValueError('Duplicate resource')
                result[address] = settings
    return result


def validate_source(hcl, identity, policy=None):
    policy = policy_config(policy)
    config = validate_aws_configuration(hcl, SimpleNamespace(**identity) if identity else None)
    if set(config) - {'terraform', 'provider', 'variable', 'locals', 'resource', 'output'}:
        raise ValueError('Data, import, moved, removed, module and ephemeral blocks are unsupported')
    for function in re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', hcl):
        if function not in FUNCTIONS:
            raise ValueError('Unsupported function; filesystem and executable functions are forbidden')
    if re.search(r'\bpath\s*\.|\bterraform\s*\.\s*workspace', hcl):
        raise ValueError('Host paths and workspace-dependent expressions are unsupported')
    for block in config.get('provider', []):
        for name, settings in block.items():
            if name not in ('aws', 'random', 'tls') or (name != 'aws' and settings):
                raise ValueError('Unsupported provider configuration')
    for block in config.get('terraform', []):
        if set(block) - {'required_version', 'required_providers'}:
            raise ValueError('Unsupported Terraform setting')
        for requirement in block.get('required_providers', []):
            if set(requirement) - {'aws', 'random', 'tls'}:
                raise ValueError('Unsupported provider')
    entries = resources(config)
    if len(entries) > 30:
        raise ValueError('Resource count exceeds policy limit')
    for address, settings in entries.items():
        kind = address.split('.')[0]
        if kind not in FIELDS or set(settings) - FIELDS[kind]:
            raise ValueError('Unsupported resource or argument: ' + kind)
        if 'tags' in settings and (not isinstance(settings['tags'], dict) or 'ACAWorkspace' in settings['tags']):
            raise ValueError('Ownership tags are controlled by the runner')
        if kind == 'tls_private_key' and (address != 'tls_private_key.ssh' or settings != {'algorithm': 'RSA', 'rsa_bits': 4096}):
            raise ValueError('Only the server-exported RSA SSH key is supported')
        if kind == 'random_id' and (not isinstance(settings.get('byte_length'), int) or not 4 <= settings['byte_length'] <= 16):
            raise ValueError('Random suffix length is outside policy')
    # TLS private material may flow only to the runner-controlled export, not locals,
    # tags or user outputs. Public key attachment is the one permitted source reference.
    for address, settings in entries.items():
        for field, value in settings.items():
            if address.startswith('aws_key_pair.') and field == 'public_key' and value == '${tls_private_key.ssh.public_key_openssh}':
                continue
            if re.search(r'\btls_private_key\b', json.dumps(value)):
                raise ValueError('SSH key references are restricted to the controlled public-key attachment')
    for section in ('locals', 'variable', 'output', 'provider'):
        if re.search(r'\btls_private_key\b', json.dumps(config.get(section, []))):
            raise ValueError('SSH key aliases and user exports are unsupported')
    for block in config.get('output', []):
        for name, settings in block.items():
            if name == 'aca_private_key' or 'private_key' in json.dumps(settings):
                raise ValueError('Private key output is managed by the runner')
    return config


def server_override(config, workspace_id):
    result = {'terraform': {'required_version': '= ' + VERSIONS['terraform']['version'],
                            'required_providers': {name: {'source': 'hashicorp/' + name, 'version': '= ' + VERSIONS[name]['version']} for name in ('aws', 'random', 'tls')}},
              'resource': {}}
    for address, settings in resources(config).items():
        kind, name = address.split('.')
        if kind in TAGGABLE:
            result['resource'].setdefault(kind, {})[name] = {'tags': {**settings.get('tags', {}), 'ACAWorkspace': workspace_id}}
    if 'tls_private_key.ssh' in resources(config):
        result['output'] = {'aca_private_key': {'value': '${tls_private_key.ssh.private_key_pem}', 'sensitive': True}}
    return result


def _reference_allowed(expression, kind, entries):
    match = re.fullmatch(r'\$\{(' + re.escape(kind) + r'\.[A-Za-z_][A-Za-z0-9_]*)\.(?:id|key_name)\}', str(expression))
    return bool(match and match.group(1) in entries)


def _target(value, expression, kind, entries, policy):
    if _reference_allowed(expression, kind, entries):
        return
    if not isinstance(value, str) or value not in policy['authorized_targets'].get(kind, []):
        raise ValueError('Resource target is unknown or not explicitly authorized: ' + kind)


def _rules(rules, policy, ingress):
    if not isinstance(rules, list) or len(rules) > 16:
        raise ValueError('Unknown or excessive security group rules')
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('protocol') not in ('tcp', '6'):
            raise ValueError('Only explicit TCP security group rules are supported')
        start, end = rule.get('from_port'), rule.get('to_port')
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= 65535:
            raise ValueError('Unknown or invalid port range')
        if rule.get('self') or rule.get('security_groups') or rule.get('prefix_list_ids'):
            raise ValueError('Only explicit CIDR rules are supported')
        cidrs = (rule.get('cidr_blocks') or []) + (rule.get('ipv6_cidr_blocks') or [])
        if not cidrs:
            raise ValueError('Unknown network rule source')
        for cidr in cidrs:
            network = ipaddress.ip_network(cidr)
            if ingress and (start <= 22 <= end or start <= 3389 <= end):
                if not any(network.version == ipaddress.ip_network(allowed).version and network.subnet_of(ipaddress.ip_network(allowed)) for allowed in policy['admin_cidrs']):
                    raise ValueError('Administrative ingress requires an explicitly authorized CIDR')
            if network.prefixlen == 0 and (start != end or start not in (80, 443)):
                raise ValueError('World-open network rules are limited to single TCP ports 80/443')


def _settings(kind, values, unknown, policy):
    def known(key):
        if unknown.get(key) or key not in values or values[key] is None:
            raise ValueError('Unknown policy-critical value: ' + key)
        return values[key]
    if kind in ('aws_vpc', 'aws_subnet'):
        network = ipaddress.ip_network(known('cidr_block'))
        private = [ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
        minimum = 16 if kind == 'aws_vpc' else 24
        if network.version != 4 or not minimum <= network.prefixlen <= 28 or not any(network.subnet_of(parent) for parent in private):
            raise ValueError('VPC/subnet CIDRs must be bounded RFC1918 networks')
    if kind == 'aws_s3_bucket' and known('force_destroy') is not False:
        raise ValueError('Forced bucket deletion is not supported')
    if kind == 'aws_s3_bucket_public_access_block':
        for flag in ('block_public_acls', 'ignore_public_acls', 'block_public_policy', 'restrict_public_buckets'):
            if known(flag) is not True:
                raise ValueError('All S3 public access blocks must be enabled')
    if kind == 'aws_s3_bucket_versioning':
        if known('versioning_configuration')[0].get('status') != 'Enabled':
            raise ValueError('S3 versioning must be enabled')
    if kind == 'aws_s3_bucket_server_side_encryption_configuration':
        if known('rule')[0].get('apply_server_side_encryption_by_default', [{}])[0].get('sse_algorithm') != 'AES256':
            raise ValueError('Only explicit AES256 bucket encryption is supported')
    if kind == 'aws_security_group':
        _rules(known('ingress'), policy, True); _rules(known('egress'), policy, False)
    if kind == 'aws_instance':
        if known('ami') not in policy['allowed_amis'] or known('instance_type') not in ('t3.nano', 't3.micro', 't3.small', 't4g.nano', 't4g.micro', 't4g.small'):
            raise ValueError('EC2 AMI/type is not approved')
        if known('associate_public_ip_address') is not False or known('metadata_options')[0].get('http_tokens') != 'required':
            raise ValueError('EC2 requires private addressing and IMDSv2')
        root = known('root_block_device')[0]
        if root.get('encrypted') is not True or not isinstance(root.get('volume_size'), int) or not 8 <= root['volume_size'] <= 30:
            raise ValueError('EC2 requires an encrypted 8–30 GiB root volume')
    if kind == 'aws_subnet' and known('map_public_ip_on_launch') is not False:
        raise ValueError('Subnets must not assign public IPs')


def validate_plan(plan, config, workspace_id, policy=None, destroy=False, owned=None):
    policy = policy_config(policy)
    if isinstance(plan, dict) and 'resource_changes' not in plan and not resources(config):
        plan = {**plan, 'resource_changes': []}
    if not isinstance(plan, dict) or not isinstance(plan.get('resource_changes'), list):
        raise ValueError('Structured Terraform plan is missing')
    owned = owned or {}
    entries = resources(config)
    counts = {'resources_to_add': 0, 'resources_to_change': 0, 'resources_to_destroy': 0}
    for item in plan['resource_changes']:
        address, kind = item.get('address'), item.get('type')
        if item.get('mode') != 'managed' or address not in entries or kind not in FIELDS or item.get('provider_name') != 'registry.terraform.io/hashicorp/' + kind.split('_')[0]:
            raise ValueError('Plan contains an unsupported resource/provider/address')
        change = item.get('change', {})
        actions = change.get('actions')
        if actions not in (['no-op'], ['create'], ['update'], ['delete']):
            raise ValueError('Replacement/import/unknown actions require a different workflow')
        if destroy and actions not in (['delete'], ['no-op']):
            raise ValueError('Destroy review may contain only deletion/no-op actions')
        before, after, unknown = change.get('before') or {}, change.get('after') or {}, change.get('after_unknown') or {}
        if actions != ['create'] and (not isinstance(before.get('id'), str) or owned.get(address) != before['id']):
            raise ValueError('Existing resource is not in the runner ownership ledger')
        if actions != ['create'] and kind in TAGGABLE and before.get('tags', {}).get('ACAWorkspace') != workspace_id:
            raise ValueError('Existing resource ownership was not established')
        if actions in (['create'], ['update'], ['no-op']):
            if kind in TAGGABLE and (unknown.get('tags') or after.get('tags', {}).get('ACAWorkspace') != workspace_id):
                raise ValueError('Runner ownership tag is missing or unknown')
            _settings(kind, after, unknown, policy)
            for field, target in TARGET_FIELDS.items():
                if field == 'bucket' and kind == 'aws_s3_bucket' or field == 'key_name' and kind == 'aws_key_pair':
                    continue
                if field not in FIELDS[kind]:
                    continue
                value, expression = after.get(field), entries[address].get(field)
                if field == 'vpc_security_group_ids':
                    if not isinstance(expression, list) or not expression:
                        raise ValueError('EC2 requires explicit owned/authorized security groups')
                    for index, expr in enumerate(expression):
                        _target(value[index] if isinstance(value, list) and index < len(value) else None, expr, target, entries, policy)
                elif expression is not None:
                    _target(value, expression, target, entries, policy)
                elif kind in ('aws_instance', 'aws_security_group', 'aws_subnet') and field != 'key_name':
                    raise ValueError('Implicit/default resource targets are not supported')
        if actions == ['create']:
            counts['resources_to_add'] += 1
        if actions == ['update']:
            counts['resources_to_change'] += 1
        if actions == ['delete']:
            counts['resources_to_destroy'] += 1
    # Every bucket created here must have corresponding safe configuration resources.
    if not destroy:
        for address in entries:
            if address.startswith('aws_s3_bucket.'):
                for companion in ('aws_s3_bucket_public_access_block', 'aws_s3_bucket_versioning', 'aws_s3_bucket_server_side_encryption_configuration'):
                    if not any(name.startswith(companion + '.') and _reference_allowed(settings.get('bucket'), 'aws_s3_bucket', {address: entries[address]}) for name, settings in entries.items()):
                        raise ValueError('Buckets require owned public-access block, versioning and encryption companions')
    return counts

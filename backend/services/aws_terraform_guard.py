"""Narrow account/region guard for the root-module Terraform workflow.

This is configuration validation, not a sandbox. Worker isolation, resource policy
and reviewed destroy plans remain separate work.
"""
import hcl2
from hcl2.utils import SerializationOptions

from services.aws_connection import AWSIdentityError

_PROVIDERS = {'aws', 'random', 'tls', 'local'}
_AWS_OPTIONS = {'region', 'allowed_account_ids', 'default_tags', 'ignore_tags', 'max_retries', 'retry_mode', 's3_use_path_style'}


def validate_aws_configuration(hcl, connection=None):
    try:
        config = hcl2.loads(hcl, serialization_options=SerializationOptions(
            with_comments=False, explicit_blocks=False, strip_string_quotes=True))
        _validate_config(config, connection)
    except AWSIdentityError:
        raise
    except Exception:
        raise AWSIdentityError('Terraform configuration could not be parsed for AWS identity checks') from None
    return config


def _validate_config(config, connection):
    if config.get('module'):
        raise AWSIdentityError('Modules are not supported by the account-bound workflow; use root resources so AWS identity can be checked.')
    for terraform in config.get('terraform', []):
        if terraform.get('backend') or terraform.get('cloud'):
            raise AWSIdentityError('Terraform state configuration must come from the server, not submitted HCL.')
        for requirement in terraform.get('required_providers', []):
            for name, settings in requirement.items():
                source = settings.get('source', f'hashicorp/{name}') if isinstance(settings, dict) else None
                if name not in _PROVIDERS or source not in (f'hashicorp/{name}', f'registry.terraform.io/hashicorp/{name}'):
                    raise AWSIdentityError('Only the server-supported hashicorp/aws, random, tls and local providers are permitted.')
    for provider in config.get('provider', []):
        for name, settings in provider.items():
            if name not in _PROVIDERS:
                raise AWSIdentityError('Unsupported Terraform provider')
            if name != 'aws':
                continue
            if set(settings) - _AWS_OPTIONS:
                raise AWSIdentityError('AWS provider credential overrides, aliases, role assumptions, custom endpoints and skipped identity checks are not supported.')
            if connection:
                _check_region(settings.get('region'), connection)
                accounts = settings.get('allowed_account_ids')
                if accounts is not None and accounts != [connection.account_id]:
                    raise AWSIdentityError('AWS provider allowed_account_ids differs from the verified account')
    for kind in ('resource', 'data', 'ephemeral'):
        for block in config.get(kind, []):
            for resource_type, resources in block.items():
                for settings in resources.values():
                    if settings.get('provisioner'):
                        raise AWSIdentityError('Provisioners are not supported in account-bound plans')
                    if resource_type.split('_', 1)[0] not in _PROVIDERS:
                        raise AWSIdentityError('Resource/data provider is not supported in account-bound plans')
                    if resource_type.startswith('aws_'):
                        if settings.get('provider') not in (None, 'aws', '${aws}'):
                            raise AWSIdentityError('AWS resources must use the guarded default provider')
                        if connection:
                            _check_region(settings.get('region'), connection)


def _check_region(region, connection):
    if region not in (None, connection.region, '${var.region}'):
        raise AWSIdentityError('Terraform region differs from the selected region. Use the verified region or var.region.')

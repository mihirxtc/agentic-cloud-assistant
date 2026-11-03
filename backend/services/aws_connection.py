"""Verified, operation-scoped AWS credentials shared by SDK and Terraform calls."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import asyncio
import inspect
import os
import re

import boto3
from botocore.config import Config

_active_connection = ContextVar('aca_aws_connection', default=None)


class AWSIdentityError(ValueError):
    """A safe, actionable identity error; never contains SDK credential details."""


@dataclass(frozen=True)
class AWSConnection:
    account_id: str
    region: str
    arn: str
    partition: str
    verified_at: str
    session: object = field(repr=False)
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    token: str | None = field(default=None, repr=False)

    @property
    def identity(self):
        return {name: getattr(self, name) for name in ('account_id', 'region', 'arn', 'partition', 'verified_at')}

    @property
    def cache_key(self):
        return self.partition, self.account_id, self.region, self.arn

    def client(self, service, **kwargs):
        if kwargs.get('region_name') == '':
            kwargs['region_name'] = self.region
        return self.session.client(service, **kwargs)

    def require_identity(self, expected):
        if not isinstance(expected, dict) or not all(expected.get(key) for key in ('account_id', 'region', 'partition')):
            raise AWSIdentityError('This record has no verified AWS identity. Create a new plan/scan; legacy records are not automatically trusted.')
        if any(expected[key] != getattr(self, key) for key in ('account_id', 'region', 'partition')):
            raise AWSIdentityError('AWS account or region differs from the recorded operation. Restore its original connection or create a new plan/scan.')


def verify_connection(region='') -> AWSConnection:
    """Resolve the server credential chain once and verify the exact frozen set."""
    try:
        region = region or os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'
        if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d+', region):
            raise AWSIdentityError('Invalid AWS region')
        source = boto3.Session(region_name=region)
        credentials = source.get_credentials()
        if credentials is None:
            raise AWSIdentityError('Server AWS credentials are unavailable. Configure a role, profile or server credentials.')
        frozen = credentials.get_frozen_credentials()
        if not frozen.access_key or not frozen.secret_key:
            raise AWSIdentityError('Server AWS credentials are incomplete')
        session = boto3.Session(aws_access_key_id=frozen.access_key, aws_secret_access_key=frozen.secret_key,
                                aws_session_token=frozen.token, region_name=region)
        caller = session.client('sts', config=Config(connect_timeout=5, read_timeout=10,
                                                    retries={'total_max_attempts': 2})).get_caller_identity()
        account, arn = caller.get('Account', ''), caller.get('Arn', '')
        partition = session.get_partition_for_region(region)
        if not re.fullmatch(r'\d{12}', account) or not arn.startswith(f'arn:{partition}:') or arn.split(':')[4] != account:
            raise AWSIdentityError('AWS returned an invalid account identity')
        pinned = os.getenv('ACA_AWS_ACCOUNT_ID', '').strip()
        if pinned and pinned != account:
            raise AWSIdentityError('Server credentials do not match ACA_AWS_ACCOUNT_ID')
        return AWSConnection(account, region, arn, partition, datetime.now(timezone.utc).isoformat(),
                             session, frozen.access_key, frozen.secret_key, frozen.token)
    except AWSIdentityError:
        raise
    except Exception:
        raise AWSIdentityError('Unable to verify the server AWS identity. Check credentials, session expiry and STS connectivity.') from None


def get_connection(region='') -> AWSConnection:
    connection = _active_connection.get()
    if connection is None:
        return verify_connection(region)
    if region and region != connection.region:
        raise AWSIdentityError('A workflow cannot switch AWS regions part-way through an operation')
    return connection


def require_record_connection(execution_id, region=''):
    from services.storage import get_execution_store
    try:
        entry = get_execution_store().get(execution_id)
    except KeyError:
        raise AWSIdentityError('Execution does not exist') from None
    expected = entry.get('aws_identity')
    if not isinstance(expected, dict) or not expected.get('region'):
        raise AWSIdentityError('This execution has no verified AWS identity. Legacy apply/destroy is blocked; create a new reviewed plan.')
    connection = get_connection(region or expected['region'])
    connection.require_identity(expected)
    return connection


def require_scan_connection(scan_id, region=''):
    from services.storage import get_execution_store
    try:
        record = get_execution_store().get_scan(scan_id)
        if record.get('service_statuses', {}).get('security_groups') != 'ok':
            raise AWSIdentityError('A successful security-group scan is required for this action')
        expected = record['aws_identity']
    except (KeyError, TypeError):
        raise AWSIdentityError('A recorded, verified scan is required for this action') from None
    connection = get_connection(region or expected['region'])
    connection.require_identity(expected)
    return connection


def aws_operation(region_parameter='region', *, execution=False, scan=False, mutating=False):
    """Fresh STS verification at each boundary, one frozen connection within it.

    functools.wraps preserves tool schemas. asyncio.to_thread propagates the
    ContextVar, so blocking Terraform work uses the same verified snapshot.
    """
    def decorate(function):
        signature = inspect.signature(function)

        def prepare(args, kwargs):
            arguments = signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            values = arguments.arguments
            # Rejecting a proposal is local and must work even if AWS is offline.
            if values.get('approved') is False:
                return None
            region = values.get(region_parameter, '')
            expected = None
            if execution:
                from services.storage import get_execution_store
                try:
                    expected = get_execution_store().get(values['execution_id']).get('aws_identity')
                except KeyError:
                    raise AWSIdentityError('Execution does not exist') from None
                if not isinstance(expected, dict) or not expected.get('region'):
                    raise AWSIdentityError('Legacy execution has no verified account/region. Create a new reviewed plan.')
                region = region or expected['region']
            if scan:
                from services.storage import get_execution_store
                try:
                    record = get_execution_store().get_scan(values['scan_id'])
                    if record.get('service_statuses', {}).get('security_groups') != 'ok':
                        raise AWSIdentityError('A successful security-group scan is required for this action')
                    expected = record['aws_identity']
                except KeyError:
                    raise AWSIdentityError('A recorded, verified scan is required for this action') from None
                region = region or expected['region']
            connection = verify_connection(region)
            if expected:
                connection.require_identity(expected)
            expected_account = values.get('expected_account_id', '')
            if expected_account and expected_account != connection.account_id:
                raise AWSIdentityError('The AWS account changed since browser verification. Verify the connection again before continuing.')
            return connection

        def finish(result, connection):
            return {**result, 'aws_identity': connection.identity} if connection is not None and isinstance(result, dict) else result

        def invalidate(connection):
            if mutating and connection:
                from services.aws_cache import invalidate_account
                invalidate_account(connection)

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                connection = await asyncio.to_thread(prepare, args, kwargs)
                token = _active_connection.set(connection)
                try:
                    return finish(await function(*args, **kwargs), connection)
                finally:
                    _active_connection.reset(token)
                    invalidate(connection)
            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            connection = prepare(args, kwargs)
            token = _active_connection.set(connection)
            try:
                return finish(function(*args, **kwargs), connection)
            finally:
                _active_connection.reset(token)
                invalidate(connection)
        return synchronous
    return decorate

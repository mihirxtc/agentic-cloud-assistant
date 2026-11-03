import hashlib
import sqlite3

from fastapi.testclient import TestClient
import pytest

from services.auth import AuthConfig, password_hash
from services.storage import ExecutionStore

PASSWORD = 'test-password-for-unit-tests'
LOGIN_HEADERS = {'X-ACA-Client': 'browser'}


@pytest.fixture(scope='module')
def encoded_password():
    return password_hash(PASSWORD)


@pytest.fixture
def client_factory(environment, monkeypatch, encoded_password):
    _, factory, _, _ = environment
    monkeypatch.setenv('ACA_OPERATOR_USERNAME', 'operator')
    monkeypatch.setenv('ACA_OPERATOR_PASSWORD_HASH', encoded_password)
    monkeypatch.setenv('ACA_COOKIE_SECURE', 'false')

    def build(**kwargs):
        return TestClient(factory.create_app(), headers={'Origin': 'http://testserver'}, **kwargs)
    return build


def login(client, password=PASSWORD):
    return client.post('/auth/login', json={'username': 'operator', 'password': password}, headers=LOGIN_HEADERS)


@pytest.mark.parametrize('method,path', [
    ('GET', '/auth/session'), ('GET', '/auth/config'), ('GET', '/auth/aws-identity'),
    ('POST', '/tools/aws_connection_identity'), ('GET', '/docs'),
    ('GET', '/redoc'), ('GET', '/openapi.json'), ('GET', '/api/ollama/status'),
    ('GET', '/terraform/keys/unknown/test.pem'), ('POST', '/tools/health_check'),
    ('POST', '/tools/run_terraform_apply_mcp'), ('POST', '/tools/revoke_open_ingress_rule'),
    ('POST', '/rag/documents/upload'), ('POST', '/api/ollama/pull'), ('POST', '/mcp/'),
])
def test_anonymous_http_access_is_blocked(client_factory, method, path):
    with client_factory() as client:
        response = client.request(method, path)
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/health').status_code == 200
        assert client.get('/ready').status_code == 200


def test_login_uses_httponly_cookie_and_hashed_persistent_token(client_factory, environment):
    _, _, store, _ = environment
    with client_factory() as client:
        response = login(client)
        assert response.status_code == 200
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']
        assert 'Path=/' in response.headers['set-cookie']
        assert 'Domain=' not in response.headers['set-cookie']
        assert 'token' not in response.json()
        assert PASSWORD not in response.text
        assert response.headers['cache-control'] == 'no-store'
        token = client.cookies['aca_session']
        with sqlite3.connect(store.path) as connection:
            row = connection.execute('SELECT token_hash FROM sessions').fetchone()
        assert row[0] == hashlib.sha256(token.encode()).hexdigest()
        assert client.get('/auth/session').json()['csrf_token'] == response.json()['csrf_token']
        assert client.get('/docs').status_code == 200


def test_csrf_logout_revokes_session_and_prevents_replay(client_factory):
    with client_factory() as client:
        csrf = login(client).json()['csrf_token']
        token = client.cookies['aca_session']
        assert client.post('/auth/logout').status_code == 403
        assert client.post('/tools/health_check', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        assert client.get('/auth/session').status_code == 200
        assert client.post('/tools/health_check', headers={'X-CSRF-Token': csrf}).status_code == 200
        response = client.post('/auth/logout', headers={'X-CSRF-Token': csrf})
        assert response.status_code == 200
        assert client.get('/auth/session').status_code == 401
        assert client.get('/auth/session', headers={'Cookie': f'aca_session={token}'}).status_code == 401


def test_login_rotates_previous_session(client_factory):
    with client_factory() as client:
        login(client)
        old = client.cookies['aca_session']
        login(client)
        assert client.cookies['aca_session'] != old
        assert client.get('/auth/session', headers={'Cookie': f'aca_session={old}'}).status_code == 401
        assert client.get('/auth/session').status_code == 200


def test_expired_session_is_rejected(client_factory, monkeypatch):
    import services.auth as auth
    with client_factory() as client:
        result = login(client).json()
        token = client.cookies['aca_session']
        monkeypatch.setattr(auth.time, 'time', lambda: result['expires_at'] + 1)
        assert client.get('/auth/session', headers={'Cookie': f'aca_session={token}'}).status_code == 401


def test_session_survives_app_and_store_recreation(client_factory, environment, monkeypatch):
    _, factory, store, _ = environment
    with client_factory() as client:
        login(client)
        token = client.cookies['aca_session']
    import services.storage as storage
    monkeypatch.setattr(storage, '_default_store', ExecutionStore(store.data_dir, store.legacy_log))
    with TestClient(factory.create_app(), headers={'Cookie': f'aca_session={token}'}) as recreated:
        assert recreated.get('/auth/session').json()['username'] == 'operator'


def test_bearer_token_works_without_cookie_or_csrf_and_is_rotatable(client_factory, environment, monkeypatch):
    _, factory, _, _ = environment
    with client_factory() as client:
        response = client.post('/tools/health_check', headers={'Authorization': 'Bearer test-mcp-token'})
        assert response.status_code == 200
        assert client.post('/tools/health_check', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        login(client)
        # An explicit bad bearer must not fall back to an ambient valid cookie.
        assert client.get('/auth/session', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    monkeypatch.setenv('ACA_MCP_TOKEN_SHA256', hashlib.sha256(b'new-token').hexdigest())
    with TestClient(factory.create_app()) as recreated:
        assert recreated.get('/auth/config', headers={'Authorization': 'Bearer test-mcp-token'}).status_code == 401
        assert recreated.get('/auth/config', headers={'Authorization': 'Bearer new-token'}).status_code == 200


@pytest.mark.parametrize('origin', ['http://evil.test', 'null', 'http://testserver.evil.test'])
def test_cross_origin_login_and_authenticated_mutations_are_rejected(client_factory, origin):
    with client_factory() as client:
        result = login(client).json()
        assert client.post('/auth/login', json={'username': 'operator', 'password': PASSWORD},
                           headers={**LOGIN_HEADERS, 'Origin': origin}).status_code == 403
        assert client.post('/tools/health_check', headers={
            'Origin': origin, 'X-CSRF-Token': result['csrf_token'],
        }).status_code == 403


def test_login_requires_origin_custom_header_and_json(client_factory):
    with client_factory() as client:
        assert client.post('/auth/login', json={'username': 'operator', 'password': PASSWORD}).status_code == 403
        assert client.post('/auth/login', content='not json', headers=LOGIN_HEADERS).status_code == 415
        client.headers.pop('origin')
        assert login(client).status_code == 403


def test_rate_limit_ignores_spoofed_forwarded_address(client_factory):
    with client_factory() as client:
        for index in range(10):
            response = client.post('/auth/login', json={'username': 'operator', 'password': 'wrong'},
                                   headers={**LOGIN_HEADERS, 'X-Forwarded-For': str(index)})
            assert response.status_code == 401
        assert login(client).status_code == 429
        assert login(client).headers['retry-after'] == '300'


def test_validation_errors_and_configuration_never_echo_secrets(client_factory, monkeypatch):
    secrets = {'GROQ_API_KEY': 'test-provider-secret', 'ANTHROPIC_API_KEY': 'test-other-secret',
               'AWS_SECRET_ACCESS_KEY': 'test-aws-secret', 'OLLAMA_BASE_URL': 'http://private-host:11434'}
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    with client_factory() as client:
        response = login(client, 'do-not-echo-this-password' * 100)
        assert response.status_code == 422
        assert 'do-not-echo' not in response.text
        login(client)
        response = client.get('/auth/config')
        assert response.status_code == 200
        assert response.json()['providers']['groq'] is True
        assert response.json()['aws']['identity_verified'] is False
        assert all(value not in response.text for value in secrets.values())


def test_secure_cookie_and_cors_allowlist(environment, monkeypatch, encoded_password):
    _, factory, _, _ = environment
    monkeypatch.setenv('ACA_OPERATOR_PASSWORD_HASH', encoded_password)
    monkeypatch.setenv('ACA_OPERATOR_USERNAME', 'operator')
    monkeypatch.setenv('ACA_COOKIE_SECURE', 'true')
    monkeypatch.setenv('ACA_ALLOWED_ORIGINS', 'https://testserver')
    with TestClient(factory.create_app(), base_url='https://testserver', headers={'Origin': 'https://testserver'}) as client:
        response = login(client)
        assert 'Secure' in response.headers['set-cookie']
        assert '__Host-aca_session=' in response.headers['set-cookie']
        assert client.get('/auth/session').status_code == 200
        assert response.headers['access-control-allow-origin'] == 'https://testserver'
        assert response.headers['access-control-allow-credentials'] == 'true'
        assert client.options('/tools/health_check', headers={
            'Origin': 'https://evil.test', 'Access-Control-Request-Method': 'POST',
        }).status_code == 400


def test_unconfigured_server_fails_closed(environment, monkeypatch):
    _, factory, _, _ = environment
    monkeypatch.setenv('ACA_OPERATOR_PASSWORD_HASH', '')
    monkeypatch.setenv('ACA_MCP_TOKEN_SHA256', '')
    with TestClient(factory.create_app(), headers={'Origin': 'http://testserver'}) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/ready').status_code == 503
        assert client.get('/auth/config').status_code == 503
        assert login(client).status_code == 503


def test_password_change_invalidates_existing_sessions(client_factory, environment, monkeypatch):
    _, factory, _, _ = environment
    with client_factory() as client:
        login(client)
        token = client.cookies['aca_session']
    monkeypatch.setenv('ACA_OPERATOR_PASSWORD_HASH', password_hash('changed-test-password'))
    with TestClient(factory.create_app(), headers={'Cookie': f'aca_session={token}'}) as recreated:
        assert recreated.get('/auth/session').status_code == 401


def test_session_database_failure_denies_access(client_factory, environment, monkeypatch):
    _, _, store, _ = environment
    with client_factory() as client:
        login(client)

        def unavailable(*args):
            raise sqlite3.OperationalError('private database details')

        monkeypatch.setattr(store, 'session', unavailable)
        response = client.get('/auth/config')
        assert response.status_code == 503
        assert 'private database' not in response.text


@pytest.mark.parametrize('name,value', [
    ('ACA_OPERATOR_PASSWORD_HASH', 'bad-hash'), ('ACA_MCP_TOKEN_SHA256', 'plain-token'),
    ('ACA_COOKIE_SECURE', 'maybe'), ('ACA_SESSION_SECONDS', '0'),
    ('ACA_ALLOWED_ORIGINS', '*'), ('ACA_ALLOWED_ORIGINS', 'http://*'),
    ('ACA_ALLOWED_ORIGINS', 'https://example.test/path'),
])
def test_invalid_security_configuration_is_rejected(environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        AuthConfig.from_env()

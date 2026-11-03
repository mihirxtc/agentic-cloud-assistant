"""Single-operator HTTP authentication; stdio retains the local process boundary."""

from dataclasses import dataclass
import getpass
import hashlib
import os
import secrets
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

ITERATIONS = 600_000


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain between 12 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256:{ITERATIONS}:{salt.hex()}:{digest.hex()}"


def _parse_hash(encoded):
    try:
        algorithm, iterations, salt, digest = encoded.split(":")
        iterations = int(iterations)
        salt, digest = bytes.fromhex(salt), bytes.fromhex(digest)
        if algorithm != "pbkdf2_sha256" or not ITERATIONS <= iterations <= 2_000_000 or len(salt) != 16 or len(digest) != 32:
            raise ValueError
        return iterations, salt, digest
    except (ValueError, TypeError):
        raise ValueError("Invalid ACA_OPERATOR_PASSWORD_HASH; generate it with python -m services.auth hash-password") from None


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _origin(value):
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or "*" in parts.netloc or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError("ACA_ALLOWED_ORIGINS must contain exact HTTP(S) origins without paths")
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    bearer_hash: str
    origins: tuple[str, ...]
    secure_cookie: bool
    session_seconds: int

    @classmethod
    def from_env(cls):
        encoded = os.getenv("ACA_OPERATOR_PASSWORD_HASH", "").strip()
        if encoded:
            _parse_hash(encoded)
        bearer = os.getenv("ACA_MCP_TOKEN_SHA256", "").strip()
        if bearer and (len(bearer) != 64 or any(c not in "0123456789abcdef" for c in bearer)):
            raise ValueError("ACA_MCP_TOKEN_SHA256 must be a lowercase SHA-256 hex digest")
        secure = os.getenv("ACA_COOKIE_SECURE", "true").lower()
        if secure not in ("true", "false"):
            raise ValueError("ACA_COOKIE_SECURE must be true or false")
        duration = int(os.getenv("ACA_SESSION_SECONDS", "28800"))
        if not 60 <= duration <= 86400:
            raise ValueError("ACA_SESSION_SECONDS must be between 60 and 86400")
        origins = tuple(_origin(x.strip()) for x in os.getenv(
            "ACA_ALLOWED_ORIGINS", "http://localhost,http://localhost:5173,http://localhost:8000"
        ).split(",") if x.strip())
        if not origins:
            raise ValueError("ACA_ALLOWED_ORIGINS cannot be empty")
        username = os.getenv("ACA_OPERATOR_USERNAME", "admin").strip()
        if not username or len(username) > 128:
            raise ValueError("ACA_OPERATOR_USERNAME must contain between 1 and 128 characters")
        return cls(username, encoded, bearer, origins, secure == "true", duration)

    @property
    def cookie_name(self):
        return "__Host-aca_session" if self.secure_cookie else "aca_session"

    @property
    def configured(self):
        return bool(self.password_hash or self.bearer_hash)

    @property
    def tag(self):
        return _digest(self.username + ":" + self.password_hash)


class AuthService:
    def __init__(self, store, config):
        self.store = store
        self.config = config

    def authenticate(self, request):
        authorization = request.headers.get("authorization")
        if authorization is not None:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() == "bearer" and token and self.config.bearer_hash and secrets.compare_digest(_digest(token), self.config.bearer_hash):
                return {"operator": self.config.username, "kind": "bearer"}
            return None
        token = request.cookies.get(self.config.cookie_name, "")
        if not token or len(token) > 256 or not self.config.password_hash:
            return None
        session = self.store.session(_digest(token), self.config.tag, time.time())
        return {**session, "kind": "session", "token_hash": _digest(token)} if session else None

    def login(self, username, password, source, previous_token=""):
        if not self.config.password_hash:
            return None, 503
        source = "source:" + _digest(source)
        if not self.store.allow_login_attempt(source, time.time()):
            return None, 429
        iterations, salt, expected = _parse_hash(self.config.password_hash)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        valid_password = secrets.compare_digest(actual, expected)
        valid_username = secrets.compare_digest(username.encode(), self.config.username.encode())
        if not (valid_password and valid_username):
            return None, 401
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        expires = now + self.config.session_seconds
        self.store.create_session(_digest(token), csrf, self.config.username, self.config.tag,
                                  expires, now, _digest(previous_token) if previous_token else None)
        self.store.clear_login_attempts(source)
        return {"token": token, "csrf_token": csrf, "expires": expires, "username": self.config.username}, 200


class AuthenticationMiddleware:
    """Guard the entire HTTP surface, including mounted MCP and docs wrappers."""

    def __init__(self, app, auth):
        self.app = app
        self.auth = auth

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        config = self.auth.config
        path, method = request.url.path, request.method

        async def respond(status, detail):
            headers = {"Cache-Control": "no-store"}
            if status == 401:
                headers["WWW-Authenticate"] = 'Bearer realm="aca"'
            response = JSONResponse({"detail": detail}, status_code=status,
                                    headers=headers)
            await response(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        if path in ("/health", "/ready") or method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        origin = request.headers.get("origin")
        if origin is not None and origin not in config.origins:
            await respond(403, "Origin not allowed")
            return
        if path == "/auth/login" and method == "POST":
            if origin not in config.origins or request.headers.get("x-aca-client") != "browser":
                await respond(403, "Trusted browser login required")
                return
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                await respond(415, "JSON login required")
                return
            await self.app(scope, receive, private_send)
            return
        if not config.configured:
            await respond(503, "Authentication is not configured on the server")
            return
        try:
            identity = self.auth.authenticate(request)
        except (sqlite3.Error, OSError):
            await respond(503, "Session storage is unavailable")
            return
        if identity is None:
            await respond(401, "Authentication required")
            return
        if identity["kind"] == "session" and method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("x-csrf-token", "")
            if not supplied or not secrets.compare_digest(supplied.encode(), identity["csrf_token"].encode()):
                await respond(403, "CSRF validation failed")
                return
        scope.setdefault("state", {})["identity"] = identity
        await self.app(scope, receive, private_send)


def main():
    if sys.argv[1:] == ["hash-password"]:
        password = getpass.getpass("Operator password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")
        try:
            print(password_hash(password))
        except ValueError as error:
            raise SystemExit(str(error)) from None
    elif sys.argv[1:] == ["generate-token"]:
        token = secrets.token_urlsafe(32)
        print(f"Bearer token (save once): {token}\nACA_MCP_TOKEN_SHA256={_digest(token)}")
    else:
        raise SystemExit("Usage: python -m services.auth {hash-password|generate-token}")


if __name__ == "__main__":
    main()

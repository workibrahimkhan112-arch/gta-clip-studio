"""Official public-client Sign in with ChatGPT; no browser credential reuse.

All public functions are synchronous. The coordinator owns guarded API routes
and calls close() from its lifespan. Only the ephemeral loopback listener accepts
OAuth callbacks; the application's FastAPI guard needs no exceptions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from backend.core import oauth_storage as storage

_ISSUER = "https://auth.openai.com"
_DISCOVERY = _ISSUER + "/.well-known/openid-configuration"
_ENDPOINTS = {
    "authorization_endpoint": _ISSUER + "/api/accounts/authorize",
    "token_endpoint": _ISSUER + "/api/accounts/oauth/token",
    "jwks_uri": _ISSUER + "/.well-known/jwks.json",
    "revocation_endpoint": _ISSUER + "/api/accounts/oauth/revoke",
}
_RESOURCE = "https://api.openai.com/v1"
_MODELS = _RESOURCE + "/models"
_DYNAMIC_CLIENT = "dynamic_agent_client"
_PLAN_SCOPE = "chatgpt.tokens.use.direct"
_SCOPES = "openid profile email offline_access resource.invoke " + _PLAN_SCOPE
_TTL = 600
_REFRESH_MARGIN = 60
_HTTP_DEADLINE = 12.0
_TERMINAL_REFRESH_ERRORS = frozenset({
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
})
_LOCK = threading.RLock()
_ERROR: str | None = None
_PENDING: _Attempt | None = None
_RETRY_CLIENT: tuple[str, float] | None = None
_SUCCESS_PAGE = b"<!doctype html><html><head><title>ChatGPT connected</title></head><body>ChatGPT sign-in completed. You can close this window and return to GTA Clip Studio.</body></html>"
_FAILURE_PAGE = b"<!doctype html><html><head><title>ChatGPT sign-in</title></head><body>ChatGPT sign-in could not be completed. Return to GTA Clip Studio and try again.</body></html>"


class AuthError(RuntimeError):
    """Safe for a route to show to the user; never includes provider response text."""


class _ProviderError(AuthError):
    def __init__(self, message: str, *, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class _Attempt:
    state: str
    nonce: str
    verifier: str
    account_id: str | None
    client_id: str
    redirect_uri: str
    expires_at: float
    deadline: float
    server: _LoopbackServer
    consumed: bool = False
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value, maximum: int = 4096) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def _secret(value) -> bool:
    return _text(value, 65536) and value.isascii() and all(ord(c) > 32 for c in value)


def _issued_client(value) -> bool:
    return _secret(value) and len(value) <= 512 and value != _DYNAMIC_CLIENT


def _read() -> dict:
    data = storage.read()
    try:
        host = data["host_id"]
        if host is not None and (not isinstance(host, str) or not host.startswith("urn:uuid:") or str(uuid.UUID(host[9:])) != host[9:]):
            raise ValueError("host")
        accounts = data["accounts"]
        if not isinstance(accounts, list) or len(accounts) > 100:
            raise ValueError("accounts")
        identifiers, registrations = set(), set()
        for account in accounts:
            if not isinstance(account, dict):
                raise ValueError("account")
            identifier = account["id"]
            pair = (account["client_id"], account["subject"])
            if not _text(identifier, 128) or identifier in identifiers or pair in registrations:
                raise ValueError("identity")
            if not _issued_client(pair[0]) or not _text(pair[1], 512) or account["issuer"] != _ISSUER:
                raise ValueError("registration")
            if not _text(account["label"], 160) or (account["email"] is not None and not _text(account["email"], 320)):
                raise ValueError("label")
            credentials = account.get("credentials")
            if credentials is not None:
                if not isinstance(credentials, dict) or not _secret(credentials["access_token"]) or not _secret(credentials["id_token"]):
                    raise ValueError("tokens")
                if credentials.get("refresh_token") is not None and not _secret(credentials["refresh_token"]):
                    raise ValueError("refresh")
                expiry = credentials["expires_at"]
                if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or not 0 < expiry < 253402300799:
                    raise ValueError("expiry")
                if not _secret(credentials["nonce"]) or credentials["token_type"] != "Bearer":
                    raise ValueError("credentials")
                if not isinstance(credentials["scopes"], list) or any(not _text(scope, 256) for scope in credentials["scopes"]):
                    raise ValueError("scopes")
            identifiers.add(identifier)
            registrations.add(pair)
        if data["active_account_id"] is not None and data["active_account_id"] not in identifiers:
            raise ValueError("active")
        if accounts and host is None:
            raise ValueError("host")
        return data
    except (KeyError, TypeError, ValueError):
        raise storage.StorageError("Saved ChatGPT registrations are invalid. Restore protected storage or reconnect using a clean credential store.") from None


def _account(data: dict, identifier: str | None) -> dict:
    for account in data["accounts"]:
        if account["id"] == identifier:
            return account
    raise AuthError("Choose a saved ChatGPT account or continue with a new account.")


def _client() -> httpx.Client:
    # Do not send credentials through environment-configured HTTP proxies.
    return httpx.Client(timeout=httpx.Timeout(5.0), follow_redirects=False, trust_env=False)


def _request(method: str, url: str, *, data=None, token: str | None = None, empty: bool = False) -> dict:
    if url not in {_DISCOVERY, _MODELS, *_ENDPOINTS.values()}:
        raise AuthError("ChatGPT authentication endpoint is not trusted.")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    deadline = time.monotonic() + _HTTP_DEADLINE
    try:
        with _client() as client:
            with client.stream(method, url, data=data, headers=headers) as response:
                if empty and response.status_code == 200:
                    return {}
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise AuthError("ChatGPT returned an unexpected response encoding.")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1024 * 1024:
                        raise AuthError("ChatGPT returned an oversized response.")
                    if time.monotonic() >= deadline:
                        raise _ProviderError("ChatGPT took too long to respond. Try again shortly.", retryable=True)
                if response.status_code != 200:
                    # Only recognized machine codes leave this boundary, never provider text.
                    code = None
                    try:
                        error = json.loads(body).get("error")
                        candidate = error.get("code") if isinstance(error, dict) else error
                        if isinstance(candidate, str) and candidate in _TERMINAL_REFRESH_ERRORS | {"invalid_client"}:
                            code = candidate
                    except (ValueError, AttributeError, TypeError):
                        pass
                    raise _ProviderError(
                        "ChatGPT request was not accepted. Try signing in again, or check ChatGPT Settings and your network.",
                        code=code, retryable=500 <= response.status_code < 600)
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("object required")
                return result
    except AuthError:
        raise
    except httpx.TransportError:
        raise _ProviderError("ChatGPT could not be reached. Check your network and try again.", retryable=True) from None
    except Exception:
        # HTTP exceptions can contain full URLs, headers, or provider token text.
        raise AuthError("ChatGPT returned an invalid response. Try again shortly.") from None


def _discovery() -> dict:
    result = _request("GET", _DISCOVERY)
    if result.get("issuer") != _ISSUER or any(result.get(key) != value for key, value in _ENDPOINTS.items()):
        raise AuthError("ChatGPT discovery did not match the official authentication endpoints.")
    if "RS256" not in result.get("id_token_signing_alg_values_supported", []):
        raise AuthError("ChatGPT discovery does not advertise the required signing algorithm.")
    return result


def _verify_id_token(encoded: str, client_id: str, nonce: str, *, refresh: bool = False) -> dict:
    try:
        import jwt
        header = jwt.get_unverified_header(encoded)
        if header.get("alg") != "RS256" or not _text(header.get("kid"), 256):
            raise ValueError("signing header")
        # Never follow a JWT-supplied jku/x5u, or trust a key embedded in the JWT.
        keys = _request("GET", _ENDPOINTS["jwks_uri"]).get("keys")
        if not isinstance(keys, list) or len(keys) > 100:
            raise ValueError("keys")
        matching = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]
                    and key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256"
                    and key.get("use", "sig") == "sig" and "verify" in key.get("key_ops", ["verify"])]
        if len(matching) != 1:
            raise ValueError("key")
        key = jwt.PyJWK.from_dict(matching[0], algorithm="RS256").key
        if key.key_size < 2048:
            raise ValueError("weak signing key")
        required = ["iss", "aud", "exp", "iat", "sub"] + ([] if refresh else ["nonce"])
        claims = jwt.decode(encoded, key, algorithms=["RS256"], audience=client_id, issuer=_ISSUER,
                            leeway=30, options={"require": required})
        if not _text(claims.get("sub"), 512):
            raise ValueError("subject")
        for claim in ("iat", "exp"):
            value = claims[claim]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("timestamp")
        if claims["exp"] <= claims["iat"]:
            raise ValueError("lifetime")
        if (not refresh or "nonce" in claims) and (not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce)):
            raise ValueError("nonce")
        audience = claims["aud"]
        if (isinstance(audience, list) and len(audience) > 1 or "azp" in claims) and claims.get("azp") != client_id:
            raise ValueError("authorized party")
        return claims
    except Exception:
        raise AuthError("ChatGPT identity verification failed. Start a fresh sign-in.") from None


def _credentials(response: dict, client_id: str, nonce: str, previous: dict | None = None) -> dict:
    if "client_id" in response and response["client_id"] != client_id:
        raise AuthError("ChatGPT returned a different client registration. Start a fresh sign-in.")
    access = response.get("access_token")
    refresh = response.get("refresh_token")
    identity = response.get("id_token", previous.get("id_token") if previous else None)
    duration = response.get("expires_in")
    token_type = response.get("token_type")
    if (not _secret(access) or not _secret(identity) or (refresh is not None and not _secret(refresh))
            or not isinstance(token_type, str) or token_type.lower() != "bearer"
            or isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not 0 < duration <= 31 * 86400):
        raise AuthError("ChatGPT returned an incomplete token set. Start a fresh sign-in.")
    scopes = response.get("scope")
    if scopes is None:
        granted = list(previous["scopes"]) if previous else []
    elif isinstance(scopes, str) and len(scopes) <= 4096 and all(_text(scope, 256) for scope in scopes.split()):
        granted = list(dict.fromkeys(scopes.split()))
    else:
        raise AuthError("ChatGPT returned invalid permissions. Start a fresh sign-in.")
    if refresh is None and (previous is not None or "offline_access" in granted):
        raise AuthError("ChatGPT did not return a renewable token set. Continue with this saved account again.")
    return {"access_token": access, "refresh_token": refresh, "id_token": identity, "token_type": "Bearer",
            "scopes": granted, "expires_at": time.time() + duration, "nonce": nonce}


class _LoopbackServer(HTTPServer):
    attempt: _Attempt
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2.0)
        return connection, address


class _DeadlineReader(io.RawIOBase):
    """A slow local peer must not extend the callback's lifetime byte by byte."""
    def __init__(self, connection: socket.socket, deadline: float):
        super().__init__()
        self.connection = connection
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Callback request deadline exceeded")
        self.connection.settimeout(min(2.0, remaining))
        return self.connection.recv_into(buffer)


class _CallbackHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.rfile.close()
        attempt = cast(_LoopbackServer, self.server).attempt
        self.rfile = io.BufferedReader(_DeadlineReader(
            self.connection, min(attempt.deadline, time.monotonic() + 2.0)))

    def log_message(self, format, *args):
        # BaseHTTPRequestHandler otherwise logs the authorization code and state.
        pass

    def send_error(self, code, message=None, explain=None):
        # The base handler can reflect malformed request lines, including query secrets.
        self._respond(False, code)

    def _respond(self, ok: bool, code: int | None = None):
        body = _SUCCESS_PAGE if ok else _FAILURE_PAGE
        try:
            self.send_response(code if code is not None else 200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True

    def do_GET(self):
        attempt = cast(_LoopbackServer, self.server).attempt
        ok = False
        try:
            location = urlsplit(self.path)
            expected_host = urlsplit(attempt.redirect_uri).netloc
            if (len(self.path) <= 16384 and location.path == "/auth/callback" and not location.scheme
                    and not location.netloc and not location.fragment
                    and self.headers.get_all("Host", []) == [expected_host]):
                parameters = parse_qs(location.query, keep_blank_values=True, max_num_fields=12)
                ok = _callback(attempt, parameters)
        except Exception:
            # The browser gets only static text, even on a storage/library error.
            pass
        self._respond(ok)


def _listen(attempt: _Attempt) -> None:
    try:
        while not attempt.stop.is_set() and time.monotonic() < attempt.deadline:
            attempt.server.handle_request()
    finally:
        attempt.server.server_close()


def _cancel_pending() -> _Attempt | None:
    global _PENDING, _RETRY_CLIENT
    attempt = _PENDING
    _PENDING = None
    _RETRY_CLIENT = None
    if attempt is not None:
        attempt.stop.set()
    return attempt


def _expire() -> None:
    global _ERROR, _RETRY_CLIENT
    if _RETRY_CLIENT is not None and time.monotonic() >= _RETRY_CLIENT[1]:
        _RETRY_CLIENT = None
    if _PENDING is not None and time.monotonic() >= _PENDING.deadline:
        _cancel_pending()
        _ERROR = "ChatGPT sign-in expired. Continue with ChatGPT to try again."


def start_login(account_id: str | None = None) -> dict:
    """Prepare a 10-minute, single-use loopback flow; caller opens its URL."""
    global _PENDING, _ERROR
    with _LOCK:
        _expire()
        discovery = _discovery()
        retry_client = _RETRY_CLIENT[0] if account_id is None and _RETRY_CLIENT is not None else None
        with storage.locked():
            data = _read()
            selected = _account(data, account_id) if account_id is not None else None
            if data["host_id"] is None:
                data["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                storage.write(data)
            host_id = data["host_id"]
        _cancel_pending()
        try:
            server = _LoopbackServer(("127.0.0.1", 0), _CallbackHandler)
        except OSError:
            raise AuthError("A private ChatGPT callback listener could not be started. Check local firewall permissions.") from None
        server.timeout = 0.2
        attempt = _Attempt(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32), verifier=secrets.token_urlsafe(64),
                           account_id=account_id, client_id=selected["client_id"] if selected else retry_client or _DYNAMIC_CLIENT,
                           redirect_uri=f"http://127.0.0.1:{server.server_address[1]}/auth/callback",
                           expires_at=time.time() + _TTL, deadline=time.monotonic() + _TTL, server=server)
        server.attempt = attempt
        challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        parameters = {"client_id": attempt.client_id, "ext_agent_host_id": host_id, "response_type": "code",
                      "redirect_uri": attempt.redirect_uri, "scope": _SCOPES, "resource": _RESOURCE,
                      "state": attempt.state, "nonce": attempt.nonce, "code_challenge_method": "S256", "code_challenge": challenge}
        if attempt.client_id == _DYNAMIC_CLIENT:
            parameters["agent_name_hint"] = "GTA Clip Studio"
        elif selected is not None:
            if (selected.get("credentials") or {}).get("id_token"):
                parameters["id_token_hint"] = selected["credentials"]["id_token"]
            if selected.get("credentials") and _PLAN_SCOPE not in selected["credentials"]["scopes"]:
                parameters["prompt"] = "consent"
            if selected["email"]:
                parameters["login_hint"] = selected["email"]
        _PENDING = attempt
        _ERROR = None
        attempt.thread = threading.Thread(target=_listen, args=(attempt,), name="chatgpt-oauth-loopback", daemon=True)
        try:
            attempt.thread.start()
        except Exception:
            _cancel_pending()
            server.server_close()
            raise AuthError("The private ChatGPT callback listener could not be started.") from None
        return {"authorization_url": discovery["authorization_endpoint"] + "?" + urlencode(parameters), "expires_at": _utc(attempt.expires_at)}


def _callback(attempt: _Attempt, parameters: dict[str, list[str]]) -> bool:
    global _PENDING, _ERROR, _RETRY_CLIENT
    with _LOCK:
        _expire()
        if _PENDING is not attempt or attempt.consumed:
            return False
        states = parameters.get("state", [])
        if len(states) != 1 or not _secret(states[0]) or not hmac.compare_digest(states[0], attempt.state):
            _ERROR = "ChatGPT sign-in state did not match. Try the original sign-in window."
            return False
        attempt.consumed = True
        attempt.stop.set()
        client_id = None
        try:
            if "error" in parameters:
                raise AuthError("ChatGPT sign-in was not authorized. Continue with ChatGPT to try again.")
            codes = parameters.get("code", [])
            clients = parameters.get("client_id", [])
            if len(codes) != 1 or not _secret(codes[0]) or len(clients) > 1:
                raise AuthError("ChatGPT sign-in callback was incomplete. Start a fresh sign-in.")
            if attempt.client_id == _DYNAMIC_CLIENT:
                if len(clients) != 1 or not _issued_client(clients[0]):
                    raise AuthError("ChatGPT registration did not return an issued client ID. Start a fresh sign-in.")
                client_id = clients[0]
            else:
                client_id = attempt.client_id
                if clients and clients[0] != client_id:
                    raise AuthError("ChatGPT returned a different client registration. Start a fresh sign-in.")
            response = _request("POST", _ENDPOINTS["token_endpoint"], data={
                "grant_type": "authorization_code", "client_id": client_id, "code": codes[0],
                "code_verifier": attempt.verifier, "redirect_uri": attempt.redirect_uri, "resource": _RESOURCE})
            credentials = _credentials(response, client_id, attempt.nonce)
            claims = _verify_id_token(credentials["id_token"], client_id, attempt.nonce)
            with storage.locked():
                data = _read()
                if attempt.account_id is not None:
                    account = _account(data, attempt.account_id)
                    if account["client_id"] != client_id or account["subject"] != claims["sub"]:
                        raise AuthError("ChatGPT signed in to a different account. The saved account was not changed.")
                else:
                    account = next((a for a in data["accounts"] if a["client_id"] == client_id and a["subject"] == claims["sub"]), None)
                    if account is None:
                        if len(data["accounts"]) >= 100:
                            raise AuthError("Too many saved ChatGPT registrations.")
                        account = {"id": str(uuid.uuid4()), "client_id": client_id, "subject": claims["sub"], "issuer": _ISSUER,
                                   "email": None, "label": f"ChatGPT account {len(data['accounts']) + 1}"}
                        data["accounts"].append(account)
                email = claims.get("email")
                account["email"] = email if _text(email, 320) else None
                account["credentials"] = credentials
                data["active_account_id"] = account["id"]
                storage.write(data)
            _ERROR = None if _PLAN_SCOPE in credentials["scopes"] else "ChatGPT plan use is not enabled. Reauthorize with plan permission in ChatGPT."
            return True
        except (AuthError, storage.StorageError) as error:
            if (isinstance(error, _ProviderError) and error.code == "invalid_grant"
                    and attempt.account_id is None and client_id is not None and _issued_client(client_id)):
                # This is not a verified account yet. Keep only a short-lived retry
                # binding, never credentials or identity from the failed exchange.
                _RETRY_CLIENT = (client_id, attempt.deadline)
                _ERROR = "ChatGPT sign-in code expired. Continue with ChatGPT to retry this registration."
            else:
                _ERROR = str(error)
            return False
        except Exception:
            _ERROR = "ChatGPT sign-in could not be completed. Start a fresh sign-in."
            return False
        finally:
            _PENDING = None


def _public(data: dict) -> dict:
    active = next((account for account in data["accounts"] if account["id"] == data["active_account_id"]), None)
    credentials = active.get("credentials") if active else None
    expiry = _PENDING.expires_at if _PENDING else credentials["expires_at"] if credentials else None
    return {"connected": bool(credentials), "plan_enabled": bool(credentials and _PLAN_SCOPE in credentials["scopes"]),
            "pending": _PENDING is not None, "error": _ERROR, "active_account_id": data["active_account_id"],
            "accounts": [{"id": account["id"], "label": account["label"], "email": account["email"],
                          "active": account["id"] == data["active_account_id"]} for account in data["accounts"]],
            "expires_at": _utc(expiry) if expiry is not None else None}


def status() -> dict:
    """Public metadata only. Polling also retires expired callback attempts."""
    global _ERROR
    with _LOCK:
        _expire()
        try:
            with storage.locked():
                return _public(_read())
        except storage.StorageError as error:
            _ERROR = str(error)
            return {"connected": False, "plan_enabled": False, "pending": _PENDING is not None,
                    "error": _ERROR, "active_account_id": None, "accounts": [],
                    "expires_at": _utc(_PENDING.expires_at) if _PENDING else None}


def select_account(account_id: str) -> dict:
    global _ERROR
    with _LOCK:
        _expire()
        with storage.locked():
            data = _read()
            account = _account(data, account_id)
            if not account.get("credentials"):
                raise AuthError("Continue with this saved ChatGPT account before selecting it.")
            _cancel_pending()
            data["active_account_id"] = account_id
            storage.write(data)
            _ERROR = None
            return _public(data)


def disconnect(account_id: str | None = None) -> dict:
    """Bound revocation retries; always disconnect locally if the network fails."""
    global _ERROR
    with _LOCK:
        _cancel_pending()
        with storage.locked():
            data = _read()
            identifier = account_id if account_id is not None else data["active_account_id"]
            account = _account(data, identifier) if identifier is not None else None
            credentials = account.get("credentials") if account else None
            confirmed = not credentials
            if account is not None and credentials and credentials.get("refresh_token"):
                try:
                    for attempt in range(2):
                        try:
                            discovery = _discovery()
                            _request("POST", discovery["revocation_endpoint"], data={"token": credentials["refresh_token"],
                                     "token_type_hint": "refresh_token", "client_id": account["client_id"]}, empty=True)
                            confirmed = True
                            break
                        except _ProviderError as error:
                            if attempt or not error.retryable:
                                raise
                            time.sleep(0.25)
                except Exception:
                    confirmed = False
            if account:
                account.pop("credentials", None)
                if data["active_account_id"] == identifier:
                    data["active_account_id"] = None
                storage.write(data)
            _ERROR = None if confirmed else "Disconnected locally. Remote revocation was not confirmed; disconnect this app in ChatGPT Settings."
            return _public(data)


def get_access_token() -> str:
    """Get only the active plan-enabled token, atomically rotating near expiry."""
    global _ERROR
    with _LOCK:
        with storage.locked():
            data = _read()
            account = _account(data, data["active_account_id"])
            previous = account.get("credentials")
            if not previous:
                raise AuthError("Continue with ChatGPT before using ChatGPT plan access.")
            if _PLAN_SCOPE not in previous["scopes"]:
                raise AuthError("ChatGPT plan use is not enabled. Reauthorize with plan permission in ChatGPT.")
            if previous["expires_at"] > time.time() + _REFRESH_MARGIN:
                return previous["access_token"]
            if not previous.get("refresh_token"):
                raise AuthError("ChatGPT access expired. Continue with this saved account again.")
            try:
                discovery = _discovery()
                response = _request("POST", discovery["token_endpoint"], data={"grant_type": "refresh_token",
                                    "client_id": account["client_id"], "refresh_token": previous["refresh_token"], "resource": _RESOURCE})
                credentials = _credentials(response, account["client_id"], previous["nonce"], previous)
                if "id_token" in response:
                    claims = _verify_id_token(credentials["id_token"], account["client_id"], previous["nonce"], refresh=True)
                    if claims["sub"] != account["subject"]:
                        raise AuthError("ChatGPT refresh returned a different identity. Continue with this saved account again.")
                account["credentials"] = credentials
                storage.write(data)
                if _PLAN_SCOPE not in credentials["scopes"]:
                    raise AuthError("ChatGPT plan permission is no longer enabled. Reauthorize this account in ChatGPT.")
                _ERROR = None
                return credentials["access_token"]
            except (AuthError, storage.StorageError) as error:
                if isinstance(error, _ProviderError) and error.code in _TERMINAL_REFRESH_ERRORS:
                    account.pop("credentials", None)
                    storage.write(data)
                    _ERROR = "ChatGPT session ended. Continue with this saved account again."
                    raise AuthError(_ERROR) from None
                _ERROR = str(error)
                raise


def list_models() -> list[dict[str, str]]:
    """Fetch the active account's current visible catalog, without inference."""
    with _LOCK:
        token = get_access_token()
        response = _request("GET", _MODELS, token=token)
        models = response.get("models")
        if not isinstance(models, list):
            raise AuthError("ChatGPT returned an invalid model catalog. Refresh the account's models and try again.")
        result = []
        for model in models:
            if not isinstance(model, dict) or model.get("visibility") != "list":
                continue
            identifier, name = model.get("slug"), model.get("display_name")
            if _text(identifier, 256) and _text(name, 256):
                result.append({"id": identifier, "name": name})
        return result


def close() -> None:
    """Stop only our callback listener; never touch application/user servers."""
    with _LOCK:
        attempt = _cancel_pending()
    if attempt is not None and attempt.thread is not None and attempt.thread is not threading.current_thread():
        attempt.thread.join(timeout=3.0)

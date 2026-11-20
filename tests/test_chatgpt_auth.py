"""Offline SIWC contract/security tests; the parent runs this module serially.

HTTP uses MockTransport, signing keys are generated locally, and callback sockets
are in-memory doubles. No browser, listening port, provider credential or model.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.core import chatgpt_auth as auth
from backend.core import oauth_storage as storage


@pytest.fixture(scope="module")
def signing_keys():
    return tuple(rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))


class InertThread:
    """Exercise listener lifecycle without starting a server or background thread."""
    def __init__(self, **kwargs):
        self.started = False
        self.joined = False
        self.kwargs = kwargs

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.joined = True


class FakeServer:
    next_port = 49000

    def __init__(self, address, handler):
        assert address == ("127.0.0.1", 0)
        FakeServer.next_port += 1
        self.server_address = (address[0], FakeServer.next_port)
        self.closed = False

    def server_close(self):
        self.closed = True


class Provider:
    def __init__(self, keys):
        self.keys = keys
        self.requests = []
        self.tokens = []
        self.revocations = []
        self.catalogs = {}
        self.discovery = {"issuer": auth._ISSUER, **auth._ENDPOINTS,
                          "id_token_signing_alg_values_supported": ["RS256"]}
        key = jwt.algorithms.RSAAlgorithm.to_jwk(keys[0].public_key(), as_dict=True)
        self.jwks = {"keys": [{**key, "kid": "offline-key", "alg": "RS256", "use": "sig"}]}

    def identity(self, client_id, nonce, *, claims=None, omit=(), key=None, headers=None):
        now = int(time.time())
        values = {"iss": auth._ISSUER, "aud": client_id, "sub": "subject-one",
                  "email": "same@example.invalid", "iat": now, "exp": now + 3600, "nonce": nonce}
        values.update(claims or {})
        for name in omit:
            values.pop(name, None)
        return jwt.encode(values, key or self.keys[0], algorithm="RS256",
                          headers={"kid": "offline-key", **(headers or {})})

    def token_set(self, attempt, client_id, *, subject="subject-one", scope=None):
        index = len([request for request in self.requests if str(request.url) == auth._ENDPOINTS["token_endpoint"]])
        return {"access_token": f"private-access-{index}", "refresh_token": f"private-refresh-{index}",
                "id_token": self.identity(client_id, attempt.nonce, claims={"sub": subject}),
                "token_type": "Bearer", "expires_in": 3600,
                "scope": auth._SCOPES if scope is None else scope}

    @staticmethod
    def reply(value, request: httpx.Request) -> httpx.Response:
        if isinstance(value, Exception):
            raise value
        if callable(value):
            result = value(request)
            assert isinstance(result, httpx.Response)
            return result
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == auth._DISCOVERY:
            return self.reply(self.discovery, request)
        if url == auth._ENDPOINTS["jwks_uri"]:
            return self.reply(self.jwks, request)
        if url == auth._ENDPOINTS["token_endpoint"]:
            assert request.method == "POST"
            assert self.tokens, "Unexpected token request"
            return self.reply(self.tokens.pop(0), request)
        if url == auth._ENDPOINTS["revocation_endpoint"]:
            assert request.method == "POST"
            value = self.revocations.pop(0) if self.revocations else httpx.Response(200, content=b"")
            return self.reply(value, request)
        if url == auth._MODELS:
            assert request.method == "GET"
            return httpx.Response(200, json=self.catalogs[request.headers["Authorization"]])
        raise AssertionError("Unexpected network destination")

    def forms(self, endpoint="token_endpoint"):
        return [parse_qs(request.content.decode("ascii")) for request in self.requests
                if str(request.url) == auth._ENDPOINTS[endpoint]]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, signing_keys):
    auth.close()
    monkeypatch.setattr(storage, "_path", lambda: tmp_path / "private" / "chatgpt-auth.bin")
    monkeypatch.setattr(auth, "_ERROR", None)
    monkeypatch.setattr(auth, "_PENDING", None)
    # Replace only this module's threading binding, not Python's global module.
    monkeypatch.setattr(auth, "threading", SimpleNamespace(
        Thread=InertThread, Event=threading.Event, current_thread=threading.current_thread))
    monkeypatch.setattr(auth, "_LoopbackServer", FakeServer)
    provider = Provider(signing_keys)
    monkeypatch.setattr(auth, "_client", lambda: httpx.Client(
        transport=httpx.MockTransport(provider.handle), trust_env=False, follow_redirects=False))
    yield provider
    auth.close()


@pytest.fixture
def sign_in(isolated):
    def connect(*, client_id="oaiapp_first", subject="subject-one", account_id=None, scope=None):
        result = auth.start_login(account_id)
        attempt = pending()
        tokens = isolated.token_set(attempt, client_id, subject=subject, scope=scope)
        isolated.tokens.append(tokens)
        assert auth._callback(attempt, {"state": [attempt.state], "code": ["private-code"], "client_id": [client_id]})
        return auth.status()["active_account_id"], attempt, tokens, result
    return connect


def pending() -> auth._Attempt:
    attempt = auth._PENDING
    assert attempt is not None
    return attempt


def saved():
    with storage.locked():
        return auth._read()


def expire_active():
    with storage.locked():
        data = auth._read()
        auth._account(data, data["active_account_id"])["credentials"]["expires_at"] = time.time() + 10
        storage.write(data)


def test_initial_status_is_offline_and_secret_free(isolated):
    assert auth.status() == {"connected": False, "plan_enabled": False, "pending": False,
                             "error": None, "active_account_id": None, "accounts": [], "expires_at": None}
    assert isolated.requests == []


def test_login_uses_ephemeral_loopback_stable_host_and_fresh_pkce(isolated):
    first = auth.start_login()
    attempt = pending()
    query = parse_qs(urlsplit(first["authorization_url"]).query)
    assert urlsplit(first["authorization_url"])._replace(query="").geturl() == auth._ENDPOINTS["authorization_endpoint"]
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["agent_name_hint"] == ["GTA Clip Studio"]
    assert query["scope"] == [auth._SCOPES]
    assert query["resource"] == ["https://api.openai.com/v1"]
    assert query["redirect_uri"] == [attempt.redirect_uri]
    callback = urlsplit(attempt.redirect_uri)
    assert (callback.scheme, callback.hostname, callback.path) == ("http", "127.0.0.1", "/auth/callback")
    assert callback.port not in (8000, 5173)
    assert query["ext_agent_host_id"] == [saved()["host_id"]]
    assert saved()["host_id"].startswith("urn:uuid:")
    expected = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected]
    assert query["code_challenge_method"] == ["S256"]
    assert attempt.verifier not in first["authorization_url"]
    assert 599 <= attempt.expires_at - time.time() <= 600
    assert cast(InertThread, attempt.thread).started
    auth.start_login()
    second = pending()
    assert attempt.stop.is_set()
    assert len({attempt.state, attempt.nonce, second.state, second.nonce}) == 4
    assert attempt.verifier != second.verifier
    assert query["ext_agent_host_id"] == [saved()["host_id"]]


def test_callback_exchanges_only_issued_client_and_exact_pkce(sign_in, isolated):
    identifier, attempt, tokens, _ = sign_in()
    form, = isolated.forms()
    assert form == {"grant_type": ["authorization_code"], "client_id": ["oaiapp_first"],
                    "code": ["private-code"], "code_verifier": [attempt.verifier],
                    "redirect_uri": [attempt.redirect_uri], "resource": [auth._RESOURCE]}
    assert "client_secret" not in form
    account, = saved()["accounts"]
    assert account["id"] == identifier
    assert account["client_id"] == "oaiapp_first"
    assert account["subject"] == "subject-one"
    assert account["credentials"]["id_token"] == tokens["id_token"]
    assert auth.get_access_token() == tokens["access_token"]
    public = json.dumps(auth.status())
    for secret in (tokens["access_token"], tokens["refresh_token"], tokens["id_token"],
                   attempt.state, attempt.nonce, attempt.verifier, "private-code", "oaiapp_first", "subject-one"):
        assert secret not in public
    assert auth.status()["connected"] and auth.status()["plan_enabled"]
    assert not auth.status()["pending"]
    assert not auth._callback(attempt, {"state": [attempt.state], "code": ["private-code"], "client_id": ["oaiapp_first"]})
    assert len(isolated.forms()) == 1


@pytest.mark.parametrize("client", [None, "", "dynamic_agent_client"])
def test_new_registration_requires_an_issued_client_id(client, isolated):
    auth.start_login()
    attempt = pending()
    parameters = {"state": [attempt.state], "code": ["private-code"]}
    if client is not None:
        parameters["client_id"] = [client]
    assert not auth._callback(attempt, parameters)
    assert isolated.forms() == []
    assert saved()["accounts"] == []
    assert not auth.status()["pending"]


@pytest.mark.parametrize("state", [[], ["wrong"], ["duplicate", "duplicate"]])
def test_wrong_state_cannot_exchange_or_consume_attempt(state, isolated):
    auth.start_login()
    attempt = pending()
    assert not auth._callback(attempt, {"state": state, "code": ["private-code"], "client_id": ["oaiapp_first"]})
    assert isolated.forms() == []
    assert not attempt.consumed
    assert auth.status()["pending"]


def test_oauth_denial_is_one_time_and_never_echoes_provider_text(isolated):
    auth.start_login()
    attempt = pending()
    assert not auth._callback(attempt, {"state": [attempt.state], "error": ["access_denied"],
                                        "error_description": ["PRIVATE-provider-message"]})
    assert attempt.consumed and attempt.stop.is_set()
    assert not auth.status()["pending"]
    assert "PRIVATE" not in json.dumps(auth.status())
    assert isolated.forms() == []


def test_expiration_poll_and_close_retire_listener(isolated):
    auth.start_login()
    attempt = pending()
    attempt.deadline = time.monotonic() - 1
    result = auth.status()
    assert not result["pending"] and "expired" in result["error"]
    assert attempt.stop.is_set()
    assert not auth._callback(attempt, {"state": [attempt.state], "code": ["private-code"]})
    auth.start_login()
    current = pending()
    auth.close()
    assert current.stop.is_set() and cast(InertThread, current.thread).joined
    assert auth._PENDING is None
    auth.close()


def test_listener_closes_only_its_own_server(isolated):
    auth.start_login()
    attempt = pending()
    attempt.server.handle_request = attempt.stop.set
    auth._listen(attempt)
    assert cast(FakeServer, attempt.server).closed


def test_accounts_with_same_email_and_subject_keep_separate_registrations(sign_in):
    first, _, first_tokens, _ = sign_in()
    first_label = auth.status()["accounts"][0]["label"]
    auth.start_login()
    assert auth.status()["active_account_id"] == first
    assert auth.get_access_token() == first_tokens["access_token"]
    second, _, second_tokens, _ = sign_in(client_id="oaiapp_second")
    result = auth.status()
    assert first != second
    assert result["active_account_id"] == second
    assert len(result["accounts"]) == 2
    assert len({account["label"] for account in result["accounts"]}) == 2
    assert {account["email"] for account in result["accounts"]} == {"same@example.invalid"}
    assert result["accounts"][0]["label"] == first_label
    assert auth.select_account(first)["active_account_id"] == first
    assert auth.get_access_token() == first_tokens["access_token"]
    assert auth.select_account(second)["active_account_id"] == second
    assert auth.get_access_token() == second_tokens["access_token"]


def test_expired_new_code_retries_issued_registration_with_fresh_proof(isolated):
    auth.start_login()
    first = pending()
    isolated.tokens.append(httpx.Response(400, json={"error": "invalid_grant"}))
    assert not auth._callback(first, {"state": [first.state], "code": ["expired-code"],
                                      "client_id": ["oaiapp_retry"]})
    assert saved()["accounts"] == []
    result = auth.start_login()
    retry = pending()
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["client_id"] == ["oaiapp_retry"]
    assert "agent_name_hint" not in query
    assert retry.state != first.state and retry.nonce != first.nonce and retry.verifier != first.verifier
    isolated.tokens.append(isolated.token_set(retry, "oaiapp_retry"))
    assert auth._callback(retry, {"state": [retry.state], "code": ["fresh-code"]})
    assert saved()["accounts"][0]["client_id"] == "oaiapp_retry"
    assert isolated.forms()[-1]["client_id"] == ["oaiapp_retry"]


def test_returning_account_reuses_registration_and_retained_hints(sign_in, isolated):
    identifier, _, tokens, _ = sign_in()
    host = saved()["host_id"]
    result = auth.start_login(identifier)
    attempt = pending()
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["client_id"] == ["oaiapp_first"]
    assert query["id_token_hint"] == [tokens["id_token"]]
    assert query["login_hint"] == ["same@example.invalid"]
    assert query["ext_agent_host_id"] == [host]
    assert "agent_name_hint" not in query
    isolated.tokens.append(isolated.token_set(attempt, "oaiapp_first"))
    assert auth._callback(attempt, {"state": [attempt.state], "code": ["another-private-code"]})
    assert auth.status()["active_account_id"] == identifier
    assert len(saved()["accounts"]) == 1


@pytest.mark.parametrize("mismatch", ["client", "subject", "signature"])
def test_returning_mismatch_cannot_replace_active_credentials(mismatch, sign_in, isolated):
    identifier, _, old, _ = sign_in()
    before = saved()
    auth.start_login(identifier)
    attempt = pending()
    token_set = isolated.token_set(attempt, "oaiapp_first", subject="another-subject" if mismatch == "subject" else "subject-one")
    if mismatch == "signature":
        token_set["id_token"] = isolated.identity("oaiapp_first", attempt.nonce, key=isolated.keys[1])
    isolated.tokens.append(token_set)
    client_id = "oaiapp_other" if mismatch == "client" else "oaiapp_first"
    assert not auth._callback(attempt, {"state": [attempt.state], "code": ["private-code"], "client_id": [client_id]})
    assert saved() == before
    assert auth.get_access_token() == old["access_token"]


@pytest.mark.parametrize("claim,value", [
    ("iss", "https://attacker.invalid"), ("aud", "another-client"), ("nonce", "another-nonce"),
    ("sub", ""), ("iat", True), ("exp", "9999999999"),
    ("iat", "future"), ("exp", "expired"),
    ("azp", "another-client"), ("aud", ["oaiapp_first", "another-client"]),
])
def test_id_token_rejects_invalid_claims(claim, value, isolated):
    if value == "future":
        value = int(time.time()) + 120
    elif value == "expired":
        value = int(time.time()) - 120
    encoded = isolated.identity("oaiapp_first", "expected-nonce", claims={claim: value})
    with pytest.raises(auth.AuthError, match="identity verification failed"):
        auth._verify_id_token(encoded, "oaiapp_first", "expected-nonce")


@pytest.mark.parametrize("missing", ["iss", "aud", "exp", "iat", "sub", "nonce"])
def test_id_token_requires_security_claims(missing, isolated):
    encoded = isolated.identity("oaiapp_first", "expected-nonce", omit=[missing])
    with pytest.raises(auth.AuthError):
        auth._verify_id_token(encoded, "oaiapp_first", "expected-nonce")


def test_signature_verification_rejects_wrong_key_and_algorithm(isolated):
    wrong_key = isolated.identity("oaiapp_first", "nonce", key=isolated.keys[1])
    unsigned = jwt.encode({"sub": "subject-one"}, key=None, algorithm="none", headers={"kid": "offline-key"})
    symmetric = jwt.encode({"sub": "subject-one"}, "offline-symmetric-key-with-at-least-32-bytes", algorithm="HS256", headers={"kid": "offline-key"})
    for encoded in (wrong_key, unsigned, symmetric):
        with pytest.raises(auth.AuthError):
            auth._verify_id_token(encoded, "oaiapp_first", "nonce")


def test_id_token_uses_fixed_jwks_and_validates_authorized_party(isolated):
    encoded = isolated.identity("oaiapp_first", "nonce", headers={"jku": "https://attacker.invalid/jwks"},
                                claims={"aud": ["oaiapp_first", "other"], "azp": "oaiapp_first"})
    assert auth._verify_id_token(encoded, "oaiapp_first", "nonce")["sub"] == "subject-one"
    assert [str(request.url) for request in isolated.requests] == [auth._ENDPOINTS["jwks_uri"]]
    isolated.jwks["keys"].append(dict(isolated.jwks["keys"][0]))
    with pytest.raises(auth.AuthError):
        auth._verify_id_token(encoded, "oaiapp_first", "nonce")


def test_identity_without_plan_permission_never_allows_inference_token(sign_in):
    identifier, _, _, _ = sign_in(scope="openid profile email offline_access")
    result = auth.status()
    assert result["connected"] and not result["plan_enabled"]
    with pytest.raises(auth.AuthError, match="plan use is not enabled"):
        auth.get_access_token()
    result = auth.start_login(identifier)
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["prompt"] == ["consent"]
    assert query["scope"] == [auth._SCOPES]


def test_refresh_rotates_atomically_once_for_concurrent_callers(sign_in, isolated):
    _, _, old, _ = sign_in()
    expire_active()
    isolated.tokens.append({"access_token": "rotated-private-access", "refresh_token": "rotated-private-refresh",
                            "expires_in": 3600, "token_type": "Bearer"})
    with ThreadPoolExecutor(max_workers=4) as executor:
        result = list(executor.map(lambda _: auth.get_access_token(), range(8)))
    assert result == ["rotated-private-access"] * 8
    assert len(isolated.forms()) == 2
    form = isolated.forms()[-1]
    assert form == {"grant_type": ["refresh_token"], "client_id": ["oaiapp_first"],
                    "refresh_token": [old["refresh_token"]], "resource": [auth._RESOURCE]}
    credentials = saved()["accounts"][0]["credentials"]
    assert credentials["refresh_token"] == "rotated-private-refresh"
    assert credentials["id_token"] == old["id_token"]
    assert credentials["scopes"] == auth._SCOPES.split()
    assert "rotated-private" not in json.dumps(auth.status())


@pytest.mark.parametrize("kind", ["identity", "audience", "nonce", "missing_rotation"])
def test_invalid_refresh_never_replaces_saved_tokens(kind, sign_in, isolated):
    _, attempt, _, _ = sign_in()
    expire_active()
    before = saved()
    tokens = {"access_token": "new-private-access", "refresh_token": "new-private-refresh",
              "expires_in": 3600, "token_type": "Bearer"}
    if kind == "missing_rotation":
        tokens.pop("refresh_token")
    else:
        claims = {"sub": "other-subject"} if kind == "identity" else {"aud": "other-client"} if kind == "audience" else {"nonce": "other-nonce"}
        tokens["id_token"] = isolated.identity("oaiapp_first", attempt.nonce, claims=claims)
    isolated.tokens.append(tokens)
    with pytest.raises(auth.AuthError):
        auth.get_access_token()
    assert saved() == before


def test_refresh_id_token_may_omit_nonce_but_still_requires_signature(sign_in, isolated):
    _, _, _, _ = sign_in()
    expire_active()
    isolated.tokens.append({"access_token": "new-private-access", "refresh_token": "new-private-refresh",
                            "id_token": isolated.identity("oaiapp_first", "unused", omit=["nonce"]),
                            "expires_in": 3600, "token_type": "Bearer"})
    assert auth.get_access_token() == "new-private-access"


@pytest.mark.parametrize("code", sorted(auth._TERMINAL_REFRESH_ERRORS))
def test_terminal_refresh_error_clears_tokens_not_registration(code, sign_in, isolated):
    identifier, _, _, _ = sign_in()
    expire_active()
    host = saved()["host_id"]
    isolated.tokens.append(httpx.Response(400, json={"error": code, "error_description": "PRIVATE-response"}))
    with pytest.raises(auth.AuthError, match="session ended"):
        auth.get_access_token()
    result = auth.status()
    assert not result["connected"] and not result["plan_enabled"]
    assert result["accounts"][0]["id"] == identifier
    assert saved()["host_id"] == host
    assert "credentials" not in saved()["accounts"][0]
    assert "PRIVATE" not in json.dumps(result)
    query = parse_qs(urlsplit(auth.start_login(identifier)["authorization_url"]).query)
    assert query["client_id"] == ["oaiapp_first"]
    assert "id_token_hint" not in query


@pytest.mark.parametrize("failure", ["network", "server", "invalid_client"])
def test_temporary_refresh_or_client_failure_preserves_credentials(failure, sign_in, isolated):
    sign_in()
    expire_active()
    before = saved()
    if failure == "network":
        response = httpx.ConnectError("PRIVATE-network-detail")
    elif failure == "server":
        response = httpx.Response(503, json={"detail": "PRIVATE-response"})
    else:
        response = httpx.Response(400, json={"error": {"code": "invalid_client", "message": "PRIVATE-response"}})
    isolated.tokens.append(response)
    with pytest.raises(auth.AuthError) as error:
        auth.get_access_token()
    assert "PRIVATE" not in str(error.value)
    assert saved() == before
    assert auth.status()["connected"]


def test_refresh_scope_loss_is_saved_but_blocks_access(sign_in, isolated):
    sign_in()
    expire_active()
    isolated.tokens.append({"access_token": "new-private-access", "refresh_token": "new-private-refresh",
                            "expires_in": 3600, "token_type": "Bearer", "scope": "openid offline_access"})
    with pytest.raises(auth.AuthError, match="permission is no longer enabled"):
        auth.get_access_token()
    assert saved()["accounts"][0]["credentials"]["refresh_token"] == "new-private-refresh"
    assert not auth.status()["plan_enabled"]


def test_disconnect_revokes_then_clears_tokens_and_relogin_reuses_host(sign_in, isolated):
    identifier, _, tokens, _ = sign_in()
    host = saved()["host_id"]
    result = auth.disconnect()
    assert not result["connected"] and result["active_account_id"] is None
    assert result["error"] is None
    assert result["accounts"][0]["id"] == identifier
    assert isolated.forms("revocation_endpoint") == [{"token": [tokens["refresh_token"]],
            "token_type_hint": ["refresh_token"], "client_id": ["oaiapp_first"]}]
    assert "credentials" not in saved()["accounts"][0]
    assert saved()["host_id"] == host
    with pytest.raises(auth.AuthError):
        auth.select_account(identifier)
    result = auth.start_login(identifier)
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["client_id"] == ["oaiapp_first"] and query["ext_agent_host_id"] == [host]
    assert "id_token_hint" not in query and "agent_name_hint" not in query


@pytest.mark.parametrize("recovery", [True, False])
def test_disconnect_has_bounded_retry_and_reports_unconfirmed_revocation(recovery, sign_in, isolated, monkeypatch):
    sign_in()
    isolated.revocations = [httpx.Response(503, json={"error": "PRIVATE-server-error"}),
                            httpx.Response(200, content=b"") if recovery else httpx.ConnectError("PRIVATE-network-error")]
    sleeps = []
    # Replace only the auth module's time binding; storage lock timing stays real.
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=sleeps.append))
    result = auth.disconnect()
    assert len(isolated.forms("revocation_endpoint")) == 2
    assert sleeps == [0.25]
    assert not result["connected"]
    assert "credentials" not in saved()["accounts"][0]
    if recovery:
        assert result["error"] is None
    else:
        assert "not confirmed" in result["error"]
    assert "PRIVATE" not in json.dumps(result)


def test_disconnect_inactive_registration_preserves_current_account(sign_in):
    first, _, _, _ = sign_in()
    second, _, tokens, _ = sign_in(client_id="oaiapp_second")
    result = auth.disconnect(first)
    assert result["active_account_id"] == second
    assert result["connected"] and result["plan_enabled"]
    assert auth.get_access_token() == tokens["access_token"]


def test_models_are_current_account_visible_catalog_not_inference(sign_in, isolated):
    first, _, first_tokens, _ = sign_in()
    second, _, second_tokens, _ = sign_in(client_id="oaiapp_second")
    isolated.catalogs = {
        "Bearer " + first_tokens["access_token"]: {"models": [
            {"slug": "first-b", "display_name": "First B", "visibility": "list"},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
            {"slug": "first-a", "display_name": "First A", "visibility": "list"},
            {"id": "wrong-schema", "visibility": "list"}]},
        "Bearer " + second_tokens["access_token"]: {"models": [
            {"slug": "second", "display_name": "Second", "visibility": "list"}]}}
    assert auth.list_models() == [{"id": "second", "name": "Second"}]
    auth.select_account(first)
    assert auth.list_models() == [{"id": "first-b", "name": "First B"}, {"id": "first-a", "name": "First A"}]
    isolated.catalogs["Bearer " + first_tokens["access_token"]] = {"models": []}
    assert auth.list_models() == []
    assert not any("responses" in str(request.url) or "backend-api" in str(request.url) for request in isolated.requests)
    assert second != first


@pytest.mark.parametrize("field", ["issuer", "authorization_endpoint", "token_endpoint", "jwks_uri", "revocation_endpoint"])
def test_discovery_cannot_redirect_credentials_to_untrusted_endpoints(field, isolated):
    isolated.discovery[field] = "https://attacker.invalid/collect"
    with pytest.raises(auth.AuthError, match="official authentication endpoints"):
        auth.start_login()
    assert auth._PENDING is None
    assert [str(request.url) for request in isolated.requests] == [auth._DISCOVERY]


def test_http_redirect_is_not_followed_and_error_text_is_redacted(isolated):
    isolated.discovery = httpx.Response(302, headers={"Location": "https://attacker.invalid/PRIVATE-query"},
                                        content=b"PRIVATE-provider-body")
    with pytest.raises(auth.AuthError) as error:
        auth.start_login()
    assert "PRIVATE" not in str(error.value)
    assert len(isolated.requests) == 1
    with pytest.raises(auth.AuthError, match="not trusted"):
        auth._request("GET", "http://auth.openai.com/api/accounts/oauth/token")
    assert len(isolated.requests) == 1


def test_response_deadline_stops_a_trickling_body(isolated, monkeypatch):
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            yield b"}"

    isolated.discovery = httpx.Response(200, stream=Chunks())
    ticks = iter([0.0, auth._HTTP_DEADLINE + 1])
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(auth.AuthError, match="too long"):
        auth._request("GET", auth._DISCOVERY)


def test_http_response_size_is_bounded(isolated):
    isolated.discovery = httpx.Response(200, content=b" " * (1024 * 1024 + 1))
    with pytest.raises(auth.AuthError, match="oversized"):
        auth.start_login()


class MemorySocket:
    def __init__(self, request):
        self.incoming = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, *args):
        # Handler.setup replaces the initial file with its deadline-aware reader.
        return io.BytesIO()

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv_into(self, buffer):
        return self.incoming.readinto(buffer)

    def sendall(self, data):
        self.output.extend(data)


def browser_request(attempt, path, *, host=None, method="GET", extra=b""):
    expected_host = urlsplit(attempt.redirect_uri).netloc
    raw = (f"{method} {path} HTTP/1.1\r\nHost: {host or expected_host}\r\n".encode()
           + extra + b"Connection: close\r\n\r\n")
    connection = MemorySocket(raw)
    auth._CallbackHandler(cast(socket.socket, connection), ("127.0.0.1", 54321), attempt.server)
    return bytes(connection.output)


def test_slow_callback_peer_has_absolute_not_per_byte_deadline(isolated, monkeypatch):
    connection = MemorySocket(b"GET /auth/callback HTTP/1.1\r\n")
    reader = auth._DeadlineReader(cast(socket.socket, connection), 2.0)
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert reader.readinto(bytearray(1)) == 1
    assert connection.timeout == 2.0
    with pytest.raises(TimeoutError):
        reader.readinto(bytearray(1))


def test_browser_callback_returns_static_page_with_security_headers(isolated, capsys):
    auth.start_login()
    attempt = pending()
    tokens = isolated.token_set(attempt, "oaiapp_first")
    isolated.tokens.append(tokens)
    path = "/auth/callback?" + urlencode({"state": attempt.state, "code": "PRIVATE-code", "client_id": "oaiapp_first"})
    response = browser_request(attempt, path)
    assert response.startswith(b"HTTP/1.0 200")
    assert response.endswith(auth._SUCCESS_PAGE)
    assert b"Cache-Control: no-store" in response and b"Referrer-Policy: no-referrer" in response
    assert b"Content-Security-Policy: default-src 'none'" in response
    for secret in (attempt.state, attempt.nonce, "PRIVATE-code", tokens["id_token"], tokens["access_token"]):
        assert secret.encode() not in response
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", ["host", "duplicate_host", "path", "method", "fragment", "too_many_fields"])
def test_invalid_browser_requests_never_echo_query_or_exchange(kind, isolated):
    auth.start_login()
    attempt = pending()
    path = "/auth/callback?" + urlencode({"state": attempt.state, "code": "PRIVATE-code", "client_id": "oaiapp_first"})
    kwargs = {}
    if kind == "host":
        kwargs["host"] = "attacker.invalid"
    elif kind == "duplicate_host":
        kwargs["extra"] = b"Host: attacker.invalid\r\n"
    elif kind == "path":
        path = path.replace("/auth/callback", "/callback")
    elif kind == "method":
        kwargs["method"] = "POST"
    elif kind == "fragment":
        path += "#PRIVATE-fragment"
    else:
        path += "&" + "&".join(f"x{index}=value" for index in range(20))
    response = browser_request(attempt, path, **kwargs)
    assert not response.startswith(b"HTTP/1.0 200")
    assert response.endswith(auth._FAILURE_PAGE)
    assert b"PRIVATE" not in response and attempt.state.encode() not in response
    assert isolated.forms() == []
    assert auth.status()["pending"]


def test_malformed_request_line_uses_static_error_page(isolated):
    auth.start_login()
    attempt = pending()
    connection = MemorySocket(b"GET /auth/callback?code=PRIVATE-code BAD-VERSION\r\n\r\n")
    auth._CallbackHandler(cast(socket.socket, connection), ("127.0.0.1", 54321), attempt.server)
    assert bytes(connection.output).endswith(auth._FAILURE_PAGE)
    assert b"PRIVATE" not in connection.output


def test_credential_file_is_separate_and_persists_without_memory_state(sign_in):
    identifier, _, tokens, _ = sign_in()
    path = storage._path()
    raw = path.read_bytes()
    assert path.name == "chatgpt-auth.bin"
    if os.name == "nt":
        assert raw.startswith(storage._PREFIX)
        for token in (tokens["access_token"], tokens["refresh_token"], tokens["id_token"]):
            assert token.encode() not in raw
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    auth.close()
    auth._ERROR = None
    assert auth.status()["active_account_id"] == identifier
    assert auth.get_access_token() == tokens["access_token"]
    assert list(path.parent.glob(".chatgpt-*.tmp")) == []


def test_windows_storage_always_uses_dpapi_and_refuses_plaintext(monkeypatch, isolated):
    # Exercise the platform branch without requiring an actual Windows account.
    protected = {}

    def crypt(payload, decrypt=False):
        if decrypt:
            return protected[payload]
        opaque = b"opaque-dpapi-ciphertext-" + str(len(protected)).encode()
        protected[opaque] = payload
        return opaque

    monkeypatch.setattr(storage, "_windows", lambda: True)
    monkeypatch.setattr(storage, "_crypt", crypt)
    storage._path().parent.mkdir(mode=0o700)
    value = {"version": 1, "host_id": None, "active_account_id": None, "accounts": [], "private": "PRIVATE-token"}
    storage.write(value)
    assert b"PRIVATE-token" not in storage._path().read_bytes()
    assert storage.read() == value
    storage._path().write_text(json.dumps(value))
    with pytest.raises(storage.StorageError):
        storage.read()


@pytest.mark.skipif(os.name == "nt", reason="Unix permission and symlink semantics")
def test_unix_storage_rejects_readable_credentials_and_writable_directory(sign_in):
    sign_in()
    path = storage._path()
    path.chmod(0o644)
    result = auth.status()
    assert not result["connected"] and "permissions" in result["error"]
    path.chmod(0o600)
    path.parent.chmod(0o777)
    try:
        with pytest.raises(storage.StorageError), storage.locked():
            pass
    finally:
        path.parent.chmod(0o700)


@pytest.mark.skipif(os.name == "nt", reason="Unix symlinks need no extra privileges")
def test_unix_storage_rejects_symlinked_files_and_private_directory(tmp_path):
    path = storage._path()
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    path.parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(storage.StorageError):
        with storage.locked():
            pass
    path.parent.unlink()
    path.parent.mkdir(mode=0o700)
    (target / "credentials").write_text("{}")
    path.symlink_to(target / "credentials")
    with pytest.raises(storage.StorageError):
        storage.read()


def test_corruption_never_silently_replaces_registration(sign_in):
    sign_in()
    path = storage._path()
    path.write_bytes(b"PRIVATE-corrupted-credentials")
    result = auth.status()
    assert not result["connected"] and result["error"]
    assert "PRIVATE" not in json.dumps(result)
    with pytest.raises(storage.StorageError):
        auth.start_login()
    assert path.read_bytes() == b"PRIVATE-corrupted-credentials"


def test_failed_atomic_replace_keeps_previous_registration(sign_in, monkeypatch):
    sign_in()
    path = storage._path()
    previous = path.read_bytes()

    def fail_replace(*args):
        raise OSError("PRIVATE-filesystem-detail")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(storage.StorageError) as error:
        with storage.locked():
            data = auth._read()
            data["active_account_id"] = None
            storage.write(data)
    assert "PRIVATE" not in str(error.value)
    assert path.read_bytes() == previous
    assert list(path.parent.glob(".chatgpt-*.tmp")) == []

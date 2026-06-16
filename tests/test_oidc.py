from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from sovereignflow.domain import AuthenticationError, DependencyUnavailableError
from sovereignflow.infrastructure.oidc import (
    JwksCache,
    OidcJwtAuthenticator,
    OidcSettings,
    _claim_value,
    _load_jwks,
)


def settings(jwks_url: str = "https://identity.test/jwks") -> OidcSettings:
    return OidcSettings(
        issuer="https://identity.test",
        audience="sovereignflow",
        jwks_url=jwks_url,
        algorithms=("RS256",),
        timeout_seconds=1,
        cache_ttl_seconds=60,
        tenant_claim="tenant_id",
        roles_claim="roles",
        groups_claim="groups",
        acl_claim="acl_labels",
        clearance_claim="clearance_label",
        classification_labels_claim="classification_labels",
        external_model_claim="allow_external_model",
        diagnostic_claim="sovereignflow_diagnostics",
    )


def key_pair(key_id: str):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = key_id
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return private, jwk


def token(private, key_id: str, **overrides) -> str:
    claims = {
        "iss": "https://identity.test",
        "aud": "sovereignflow",
        "sub": "user-1",
        "exp": int(time.time()) + 300,
        "tenant_id": "tenant-a",
        "roles": ["reader"],
        "groups": ["customs"],
        "acl_labels": ["public", "internal"],
        "clearance_label": "INTERNAL",
        "classification_labels": ["US_NOFORN", "US_ORCON"],
        "allow_external_model": True,
        "sovereignflow_diagnostics": True,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": key_id})


def authenticator(private=None, jwk=None):
    if private is None or jwk is None:
        private, jwk = key_pair("key-1")
    cache = JwksCache(
        url="ignored",
        timeout_seconds=1,
        ttl_seconds=60,
        loader=lambda url, timeout: {"keys": [jwk]},
    )
    return OidcJwtAuthenticator(settings(), cache=cache), private


def test_oidc_authenticator_validates_and_maps_authorization_context() -> None:
    selected, private = authenticator()

    context = selected.authenticate(token(private, "key-1"))

    assert context.subject == "user-1"
    assert context.tenant_id == "tenant-a"
    assert context.roles == ("reader",)
    assert context.groups == ("customs",)
    assert context.acl_labels == ("internal", "public")
    assert context.security.clearance_label == "INTERNAL"
    assert context.security.classification_labels == ("US_NOFORN", "US_ORCON")
    assert context.allow_external_model is True
    assert context.diagnostic_access is True


def test_oidc_authenticator_uses_safe_defaults_for_optional_claims() -> None:
    selected, private = authenticator()
    access_token = token(
        private,
        "key-1",
        roles=[],
        groups=[],
        acl_labels=[],
        clearance_label=None,
        classification_labels=[],
        allow_external_model=False,
        sovereignflow_diagnostics=False,
    )

    context = selected.authenticate(access_token)

    assert context.roles == ()
    assert context.security.clearance_label is None
    assert context.security.classification_labels == ()
    assert context.allow_external_model is False


def test_oidc_authenticator_reads_explicit_nested_claim_paths() -> None:
    private, jwk = key_pair("nested-key")
    nested_settings = settings()
    nested_settings = OidcSettings(
        **{
            **nested_settings.__dict__,
            "roles_claim": "realm_access.roles",
            "groups_claim": "resource_access.sovereignflow.groups",
        }
    )
    selected = OidcJwtAuthenticator(
        nested_settings,
        cache=JwksCache(
            url="ignored",
            timeout_seconds=1,
            ttl_seconds=60,
            loader=lambda url, timeout: {"keys": [jwk]},
        ),
    )
    access_token = token(
        private,
        "nested-key",
        roles=None,
        groups=None,
        realm_access={"roles": ["realm-reader"]},
        resource_access={"sovereignflow": {"groups": ["application-readers"]}},
    )

    context = selected.authenticate(access_token)

    assert context.roles == ("realm-reader",)
    assert context.groups == ("application-readers",)
    assert _claim_value({"nested": {}}, "nested.missing", default=[]) == []


@pytest.mark.parametrize(
    ("access_token_factory", "message"),
    [
        (lambda private: "", "required"),
        (lambda private: jwt.encode({"sub": "x"}, private, algorithm="RS256"), "kid"),
        (lambda private: "not-a-token", "validation failed"),
        (
            lambda private: token(private, "key-1", iss="https://wrong.test"),
            "validation failed",
        ),
        (
            lambda private: token(private, "key-1", aud="wrong"),
            "validation failed",
        ),
        (
            lambda private: token(private, "key-1", exp=int(time.time()) - 1),
            "validation failed",
        ),
        (lambda private: token(private, "key-1", tenant_id=""), "tenant_id"),
        (lambda private: token(private, "key-1", roles="reader"), "string array"),
        (
            lambda private: token(private, "key-1", clearance_label=True),
            "clearance_label",
        ),
        (
            lambda private: token(private, "key-1", allow_external_model="yes"),
            "must be boolean",
        ),
    ],
)
def test_oidc_authenticator_rejects_invalid_tokens(access_token_factory, message: str) -> None:
    selected, private = authenticator()

    with pytest.raises(AuthenticationError, match=message):
        selected.authenticate(access_token_factory(private))


def test_oidc_authenticator_rejects_non_object_claims(monkeypatch) -> None:
    selected, private = authenticator()
    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: [])

    with pytest.raises(AuthenticationError, match="claims"):
        selected.authenticate(token(private, "key-1"))


def test_jwks_cache_caches_refreshes_and_rotates_keys() -> None:
    _, first = key_pair("first")
    _, second = key_pair("second")
    now = [10.0]
    payloads = iter(({"keys": [first]}, {"keys": [first, second]}, {"keys": [second]}))
    calls = []
    cache = JwksCache(
        url="jwks",
        timeout_seconds=2,
        ttl_seconds=5,
        clock=lambda: now[0],
        loader=lambda url, timeout: calls.append((url, timeout)) or next(payloads),
    )

    assert cache.key("first")["kid"] == "first"
    assert cache.key("second")["kid"] == "second"
    now[0] = 16.0
    assert cache.key("second")["kid"] == "second"
    assert calls == [("jwks", 2), ("jwks", 2), ("jwks", 2)]


def test_jwks_cache_rejects_unknown_and_invalid_key_sets() -> None:
    _, valid = key_pair("valid")
    unknown = JwksCache(
        url="jwks",
        timeout_seconds=1,
        ttl_seconds=1,
        loader=lambda url, timeout: {"keys": [valid]},
    )
    with pytest.raises(AuthenticationError, match="unknown"):
        unknown.key("missing")

    invalid_payloads = (
        {},
        {"keys": ["invalid"]},
        {"keys": [{}]},
        {"keys": []},
    )
    for payload in invalid_payloads:
        cache = JwksCache(
            url="jwks",
            timeout_seconds=1,
            ttl_seconds=1,
            loader=lambda url, timeout, value=payload: value,
        )
        with pytest.raises(DependencyUnavailableError, match="JWKS|kid"):
            cache.key("key")


def test_load_jwks_uses_real_http_protocol_and_maps_invalid_responses(http_server) -> None:
    http_server.responses[("GET", "/jwks")] = (
        200,
        {"keys": [{"kid": "key"}]},
        "application/json",
    )
    base_url = f"http://127.0.0.1:{http_server.server_address[1]}"

    assert _load_jwks(f"{base_url}/jwks", 1)["keys"][0]["kid"] == "key"

    http_server.responses[("GET", "/invalid-json")] = (
        200,
        b"{",
        "application/json",
    )
    with pytest.raises(DependencyUnavailableError, match="JSON"):
        _load_jwks(f"{base_url}/invalid-json", 1)

    http_server.responses[("GET", "/scalar")] = (200, [], "application/json")
    with pytest.raises(DependencyUnavailableError, match="invalid JWKS"):
        _load_jwks(f"{base_url}/scalar", 1)

    with pytest.raises(DependencyUnavailableError, match="unavailable"):
        _load_jwks(f"{base_url}/missing", 1)

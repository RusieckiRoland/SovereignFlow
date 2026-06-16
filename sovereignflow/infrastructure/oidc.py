from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import jwt

from sovereignflow.domain import (
    AuthenticationError,
    AuthorizationContext,
    DependencyUnavailableError,
    SubjectSecurity,
)


@dataclass(frozen=True)
class OidcSettings:
    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...]
    timeout_seconds: float
    cache_ttl_seconds: int
    tenant_claim: str
    roles_claim: str
    groups_claim: str
    acl_claim: str
    clearance_claim: str
    classification_labels_claim: str
    external_model_claim: str
    diagnostic_claim: str


class JwksCache:
    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        loader: Callable[[str, float], Mapping[str, Any]] | None = None,
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._loader = loader or _load_jwks
        self._expires_at = 0.0
        self._keys: dict[str, Mapping[str, Any]] = {}

    def key(self, key_id: str) -> Mapping[str, Any]:
        now = self._clock()
        if now >= self._expires_at:
            self._refresh(now)
        key = self._keys.get(key_id)
        if key is None:
            self._refresh(now)
            key = self._keys.get(key_id)
        if key is None:
            raise AuthenticationError("Access token signing key is unknown")
        return key

    def _refresh(self, now: float) -> None:
        payload = self._loader(self._url, self._timeout_seconds)
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise DependencyUnavailableError("Identity Provider returned invalid JWKS")
        parsed: dict[str, Mapping[str, Any]] = {}
        for item in keys:
            if not isinstance(item, dict):
                raise DependencyUnavailableError("Identity Provider returned invalid JWKS")
            key_id = str(item.get("kid") or "").strip()
            if not key_id:
                raise DependencyUnavailableError("Identity Provider returned a key without kid")
            parsed[key_id] = item
        if not parsed:
            raise DependencyUnavailableError("Identity Provider returned an empty JWKS")
        self._keys = parsed
        self._expires_at = now + self._ttl_seconds


class OidcJwtAuthenticator:
    def __init__(
        self,
        settings: OidcSettings,
        *,
        cache: JwksCache | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache or JwksCache(
            url=settings.jwks_url,
            timeout_seconds=settings.timeout_seconds,
            ttl_seconds=settings.cache_ttl_seconds,
        )

    def authenticate(self, access_token: str) -> AuthorizationContext:
        token = str(access_token or "").strip()
        if not token:
            raise AuthenticationError("Bearer access token is required")
        try:
            header = jwt.get_unverified_header(token)
            key_id = str(header.get("kid") or "").strip()
            if not key_id:
                raise AuthenticationError("Access token header does not contain kid")
            public_key = jwt.PyJWK.from_dict(dict(self._cache.key(key_id))).key
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=list(self._settings.algorithms),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (AuthenticationError, DependencyUnavailableError):
            raise
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Access token validation failed") from exc
        if not isinstance(claims, dict):
            raise AuthenticationError("Access token claims are invalid")
        return AuthorizationContext(
            subject=_required_claim(claims, "sub"),
            tenant_id=_required_claim(claims, self._settings.tenant_claim),
            roles=_string_tuple(claims, self._settings.roles_claim),
            groups=_string_tuple(claims, self._settings.groups_claim),
            acl_labels=_string_tuple(claims, self._settings.acl_claim),
            security=SubjectSecurity(
                clearance_label=_optional_string_claim(
                    claims,
                    self._settings.clearance_claim,
                ),
                classification_labels=_string_tuple(
                    claims,
                    self._settings.classification_labels_claim,
                ),
            ),
            allow_external_model=_boolean_claim(
                claims,
                self._settings.external_model_claim,
            ),
            diagnostic_access=_boolean_claim(
                claims,
                self._settings.diagnostic_claim,
            ),
        )


def _load_jwks(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise DependencyUnavailableError("Identity Provider JWKS endpoint is unavailable") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DependencyUnavailableError("Identity Provider returned invalid JWKS JSON") from exc
    if not isinstance(payload, dict):
        raise DependencyUnavailableError("Identity Provider returned invalid JWKS")
    return payload


def _required_claim(claims: Mapping[str, Any], name: str) -> str:
    value = _claim_value(claims, name)
    normalized = str(value or "").strip()
    if not normalized:
        raise AuthenticationError(f"Access token claim is required: {name}")
    return normalized


def _string_tuple(claims: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = _claim_value(claims, name, default=[])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AuthenticationError(f"Access token claim must be a string array: {name}")
    return tuple(value)


def _optional_string_claim(claims: Mapping[str, Any], name: str) -> str | None:
    value = _claim_value(claims, name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AuthenticationError(f"Access token claim must be a non-empty string: {name}")
    return value.strip()


def _boolean_claim(claims: Mapping[str, Any], name: str) -> bool:
    value = _claim_value(claims, name, default=False)
    if not isinstance(value, bool):
        raise AuthenticationError(f"Access token claim must be boolean: {name}")
    return value


def _claim_value(
    claims: Mapping[str, Any],
    path: str,
    *,
    default: Any = None,
) -> Any:
    value: Any = claims
    for segment in path.split("."):
        if not isinstance(value, Mapping) or segment not in value:
            return default
        value = value[segment]
    return value

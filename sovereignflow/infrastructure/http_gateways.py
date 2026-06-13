from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from sovereignflow.domain import (
    DependencyUnavailableError,
    ProviderProtocolError,
    ValidationError,
)


@dataclass(frozen=True)
class ModelEndpoint:
    name: str
    scope: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.scope not in {"local", "external"}:
            raise ValidationError("ModelEndpoint.scope must be 'local' or 'external'")
        if self.timeout_seconds <= 0:
            raise ValidationError("ModelEndpoint.timeout_seconds must be greater than zero")


@dataclass(frozen=True)
class EmbeddingEndpoint:
    name: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValidationError("EmbeddingEndpoint.timeout_seconds must be greater than zero")


class _JsonHttpClient:
    def get(self, *, url: str, api_key: str, timeout_seconds: float) -> dict[str, Any]:
        return self._request(
            url=url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            method="GET",
            payload=None,
        )

    def post(
        self,
        *,
        url: str,
        api_key: str,
        timeout_seconds: float,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            url=url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            method="POST",
            payload=payload,
        )

    def _request(
        self,
        *,
        url: str,
        api_key: str,
        timeout_seconds: float,
        method: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise DependencyUnavailableError(
                f"Provider request failed with HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DependencyUnavailableError("Provider endpoint is unavailable") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError("Provider returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ProviderProtocolError("Provider response must be a JSON object")
        return decoded


class OpenAIModelGateway:
    def __init__(
        self,
        endpoint: ModelEndpoint,
        *,
        http_client: _JsonHttpClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._http = http_client or _JsonHttpClient()

    @property
    def scope(self) -> str:
        return self._endpoint.scope

    def healthcheck(self) -> None:
        self._http.get(
            url=f"{self._endpoint.base_url.rstrip('/')}/models",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
        )

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        response = self._http.post(
            url=f"{self._endpoint.base_url.rstrip('/')}/chat/completions",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
            payload={
                "model": self._endpoint.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            },
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderProtocolError("Model response has an invalid schema") from exc
        normalized = str(content or "").strip()
        if not normalized:
            raise ProviderProtocolError("Model response is empty")
        return normalized


class OpenAIEmbeddingGateway:
    def __init__(
        self,
        endpoint: EmbeddingEndpoint,
        *,
        http_client: _JsonHttpClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._http = http_client or _JsonHttpClient()

    def healthcheck(self) -> None:
        self._http.get(
            url=f"{self._endpoint.base_url.rstrip('/')}/models",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
        )

    def embed_query(self, text: str) -> tuple[float, ...]:
        response = self._http.post(
            url=f"{self._endpoint.base_url.rstrip('/')}/embeddings",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
            payload={"model": self._endpoint.model, "input": text},
        )
        try:
            vector = response["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderProtocolError("Embedding response has an invalid schema") from exc
        if not isinstance(vector, list) or not vector:
            raise ProviderProtocolError("Embedding response contains no vector")
        try:
            return tuple(float(value) for value in vector)
        except (TypeError, ValueError) as exc:
            raise ProviderProtocolError("Embedding vector must contain numbers") from exc

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sovereignflow.domain import (
    DependencyUnavailableError,
    ModelGeneration,
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
    input_cost_per_million: float
    output_cost_per_million: float

    def __post_init__(self) -> None:
        if self.scope not in {"internal", "external"}:
            raise ValidationError("ModelEndpoint.scope must be 'internal' or 'external'")
        if self.timeout_seconds <= 0:
            raise ValidationError("ModelEndpoint.timeout_seconds must be greater than zero")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise ValidationError("ModelEndpoint token costs cannot be negative")


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
    def name(self) -> str:
        return self._endpoint.name

    @property
    def model_id(self) -> str:
        return self._endpoint.model

    @property
    def scope(self) -> str:
        return self._endpoint.scope

    def healthcheck(self) -> None:
        self._http.get(
            url=f"{self._endpoint.base_url.rstrip('/')}/models",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        generation_parameters: Mapping[str, Any] | None = None,
    ) -> ModelGeneration:
        payload = {
            "model": self._endpoint.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        if generation_parameters is not None:
            payload.update(generation_parameters)
        response = self._http.post(
            url=f"{self._endpoint.base_url.rstrip('/')}/chat/completions",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
            payload=payload,
        )
        try:
            content = response["choices"][0]["message"]["content"]
            prompt_tokens = int(response["usage"]["prompt_tokens"])
            completion_tokens = int(response["usage"]["completion_tokens"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderProtocolError("Model response has an invalid schema") from exc
        normalized = str(content or "").strip()
        if not normalized:
            raise ProviderProtocolError("Model response is empty")
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ProviderProtocolError("Model token usage cannot be negative")
        estimated_cost = (
            prompt_tokens * self._endpoint.input_cost_per_million
            + completion_tokens * self._endpoint.output_cost_per_million
        ) / 1_000_000
        return ModelGeneration(
            text=normalized,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=estimated_cost,
        )


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
        return self.embed_documents((text,))[0]

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        normalized = tuple(str(text or "").strip() for text in texts)
        if not normalized or any(not text for text in normalized):
            raise ValidationError("Embedding input must contain non-empty texts")
        response = self._http.post(
            url=f"{self._endpoint.base_url.rstrip('/')}/embeddings",
            api_key=self._endpoint.api_key,
            timeout_seconds=self._endpoint.timeout_seconds,
            payload={"model": self._endpoint.model, "input": list(normalized)},
        )
        try:
            rows = response["data"]
            if not isinstance(rows, list) or not rows:
                raise TypeError("data must be a non-empty list")
            if len(rows) != len(normalized):
                raise ProviderProtocolError("Embedding response count does not match input")
            ordered = sorted(rows, key=lambda item: int(item["index"]))
            if [int(item["index"]) for item in ordered] != list(range(len(normalized))):
                raise ValueError("embedding indexes must match inputs")
            vectors = tuple(item["embedding"] for item in ordered)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderProtocolError("Embedding response has an invalid schema") from exc
        if any(not isinstance(vector, list) or not vector for vector in vectors):
            raise ProviderProtocolError("Embedding response contains no vector")
        try:
            converted = tuple(tuple(float(value) for value in vector) for vector in vectors)
        except (TypeError, ValueError) as exc:
            raise ProviderProtocolError("Embedding vector must contain numbers") from exc
        dimensions = {len(vector) for vector in converted}
        if len(dimensions) != 1:
            raise ProviderProtocolError("Embedding vectors have inconsistent dimensions")
        return converted

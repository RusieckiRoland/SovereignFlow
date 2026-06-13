from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence


class ModelRoutingPolicy(StrEnum):
    LOCAL_ONLY = "local_only"
    PREFER_LOCAL = "prefer_local"
    EXTERNAL_ALLOWED = "external_allowed"


@dataclass(frozen=True)
class ModelEndpoint:
    name: str
    scope: str
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if self.scope not in {"local", "external"}:
            raise ValueError("Model endpoint scope must be 'local' or 'external'")
        for field_name in ("name", "base_url", "model"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"ModelEndpoint.{field_name} is required")


class OpenAICompatibleClient:
    def __init__(self, endpoint: ModelEndpoint) -> None:
        self.endpoint = endpoint

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        security_context: dict[str, object] | None = None,
    ) -> str:
        url = f"{self.endpoint.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.endpoint.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.endpoint.api_key:
            headers["Authorization"] = f"Bearer {self.endpoint.api_key}"

        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.endpoint.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Model endpoint '{self.endpoint.name}' failed") from exc

        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Model endpoint '{self.endpoint.name}' returned an invalid response") from exc


class PolicyRoutedModel:
    def __init__(
        self,
        clients: Sequence[OpenAICompatibleClient],
        policy: ModelRoutingPolicy = ModelRoutingPolicy.LOCAL_ONLY,
    ) -> None:
        self._clients = tuple(clients)
        self._policy = policy
        if not self._clients:
            raise ValueError("At least one model client is required")

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        security_context: dict[str, object] | None = None,
    ) -> str:
        clients = self._eligible_clients(security_context or {})
        if not clients:
            raise RuntimeError(f"No model endpoint is allowed by policy '{self._policy.value}'")

        last_error: Exception | None = None
        for client in clients:
            try:
                return client.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    security_context=security_context,
                )
            except Exception as exc:
                last_error = exc
        raise RuntimeError("All allowed model endpoints failed") from last_error

    def _eligible_clients(
        self,
        security_context: dict[str, object],
    ) -> tuple[OpenAICompatibleClient, ...]:
        local = tuple(client for client in self._clients if client.endpoint.scope == "local")
        external = tuple(client for client in self._clients if client.endpoint.scope == "external")

        if security_context.get("allow_external") is False:
            return local
        if self._policy == ModelRoutingPolicy.LOCAL_ONLY:
            return local
        if self._policy == ModelRoutingPolicy.PREFER_LOCAL:
            return local + external
        return self._clients

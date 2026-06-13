from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sovereignflow.domain import SearchHit, SearchRequest


class RetrievalPort(Protocol):
    def search(self, request: SearchRequest) -> Sequence[SearchHit]: ...

    def healthcheck(self) -> None: ...


class EmbeddingGatewayPort(Protocol):
    def embed_query(self, text: str) -> Sequence[float]: ...

    def healthcheck(self) -> None: ...


class ModelGatewayPort(Protocol):
    @property
    def scope(self) -> str: ...

    def generate(self, *, system_prompt: str, user_prompt: str) -> str: ...

    def healthcheck(self) -> None: ...


class PromptRepositoryPort(Protocol):
    def load(self, prompt_name: str) -> str: ...


class HealthProbe(Protocol):
    @property
    def name(self) -> str: ...

    def check(self) -> None: ...

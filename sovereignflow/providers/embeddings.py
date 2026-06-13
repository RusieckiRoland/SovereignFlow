from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name_or_path: str, *, device: str | None = None) -> None:
        self._model_name_or_path = model_name_or_path
        self._device = device
        self._model: Any = None

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        model = self._get_model()
        vectors = model.encode(list(texts), normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> Sequence[float]:
        return self.embed_documents([text])[0]

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Install SovereignFlow with the 'local-ai' extra to use local embeddings"
                ) from exc
            self._model = SentenceTransformer(self._model_name_or_path, device=self._device)
        return self._model


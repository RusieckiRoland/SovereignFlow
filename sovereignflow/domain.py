from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RetrievalProfile:
    mode: str = "hybrid"
    top_k: int = 8
    max_context_characters: int = 24_000
    filters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "RetrievalProfile":
        data = raw or {}
        mode = str(data.get("mode") or "hybrid").strip().lower()
        if mode not in {"semantic", "bm25", "hybrid"}:
            raise ValueError(f"Unsupported retrieval mode: {mode}")

        top_k = int(data.get("top_k") or 8)
        if top_k < 1:
            raise ValueError("retrieval.top_k must be greater than zero")

        max_context = int(data.get("max_context_characters") or 24_000)
        if max_context < 1:
            raise ValueError("retrieval.max_context_characters must be greater than zero")

        filters = data.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("retrieval.filters must be a mapping")

        return cls(
            mode=mode,
            top_k=top_k,
            max_context_characters=max_context,
            filters=dict(filters),
        )


@dataclass(frozen=True)
class DomainProfile:
    name: str
    description: str
    collection: str
    pipeline: str
    system_prompt: str
    allow_external_models: bool = False
    disclaimer: str = ""
    retrieval: RetrievalProfile = field(default_factory=RetrievalProfile)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "DomainProfile":
        required = ("name", "collection", "pipeline", "system_prompt")
        missing = [key for key in required if not str(raw.get(key) or "").strip()]
        if missing:
            raise ValueError(f"Domain profile is missing: {', '.join(missing)}")

        return cls(
            name=str(raw["name"]).strip(),
            description=str(raw.get("description") or "").strip(),
            collection=str(raw["collection"]).strip(),
            pipeline=str(raw["pipeline"]).strip(),
            system_prompt=str(raw["system_prompt"]).strip(),
            allow_external_models=bool(raw.get("allow_external_models") is True),
            disclaimer=str(raw.get("disclaimer") or "").strip(),
            retrieval=RetrievalProfile.from_mapping(raw.get("retrieval")),
        )


def load_domain_profile(path: str | Path) -> DomainProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Domain profile must contain a mapping: {profile_path}")
    profile = DomainProfile.from_mapping(raw)
    pipeline_path = Path(profile.pipeline)
    if not pipeline_path.is_absolute():
        project_relative = Path.cwd() / pipeline_path
        profile_relative = profile_path.parent / pipeline_path
        resolved = project_relative if project_relative.is_file() else profile_relative
        profile = replace(profile, pipeline=str(resolved.resolve()))
    return profile

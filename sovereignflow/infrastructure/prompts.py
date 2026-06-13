from __future__ import annotations

from pathlib import Path

from sovereignflow.domain import ConfigurationError


class FilePromptRepository:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def load(self, prompt_name: str) -> str:
        path = (self._root / f"{prompt_name}.txt").resolve()
        if self._root not in path.parents:
            raise ConfigurationError("Prompt path escapes the configured prompt directory")
        if not path.is_file():
            raise ConfigurationError(f"Prompt does not exist: {prompt_name}")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ConfigurationError(f"Prompt is empty: {prompt_name}")
        return content

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import FileStatistics


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> FileStatistics:
    count = 0
    digest = sha256()
    byte_count = 0
    with path.open("wb") as stream:
        for record in records:
            encoded = (
                json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            payload = encoded.encode("utf-8")
            stream.write(payload)
            digest.update(payload)
            byte_count += len(payload)
            count += 1
    return FileStatistics(count, digest.hexdigest(), byte_count)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(encoded + "\n", encoding="utf-8", newline="\n")

from __future__ import annotations

import ast
from pathlib import Path


def test_generator_does_not_import_sovereignflow() -> None:
    source_root = Path(__file__).parents[1] / "src"
    imported_modules = []

    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

    assert not any(
        module == "sovereignflow" or module.startswith("sovereignflow.")
        for module in imported_modules
    )

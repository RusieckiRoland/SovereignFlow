from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "sovereignflow"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_has_no_framework_or_outer_layer_dependencies() -> None:
    forbidden = {
        "flask",
        "yaml",
        "weaviate",
        "psycopg",
        "waitress",
        "sovereignflow.application",
        "sovereignflow.infrastructure",
        "sovereignflow.interfaces",
        "sovereignflow.bootstrap",
    }
    for path in (ROOT / "domain").glob("*.py"):
        imports = imported_modules(path)
        assert not any(
            module == item or module.startswith(f"{item}.")
            for module in imports
            for item in forbidden
        ), path


def test_application_has_no_infrastructure_or_interface_dependencies() -> None:
    forbidden = {
        "flask",
        "yaml",
        "weaviate",
        "psycopg",
        "waitress",
        "sovereignflow.infrastructure",
        "sovereignflow.interfaces",
        "sovereignflow.bootstrap",
    }
    for path in (ROOT / "application").rglob("*.py"):
        imports = imported_modules(path)
        assert not any(
            module == item or module.startswith(f"{item}.")
            for module in imports
            for item in forbidden
        ), path


def test_each_action_lives_in_its_own_module() -> None:
    actions_root = ROOT / "application" / "actions"
    for path in actions_root.glob("*.py"):
        if path.name.startswith("_"):
            continue
        imports = imported_modules(path)
        other_action_modules = {
            f"sovereignflow.application.actions.{p.stem}"
            for p in actions_root.glob("*.py")
            if p != path and not p.name.startswith("_")
        }
        assert not imports.intersection(other_action_modules), path


def test_core_contains_no_code_analysis_vocabulary() -> None:
    forbidden = ("roslyn", "class_name", "member_name", "sql_schema", "snapshot_id")
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8").casefold()
        assert not any(term in text for term in forbidden), path

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from dataset_generator.models import ConfigurationError, OutputConflictError
from dataset_generator.validation import OUTPUT_FILES, prepare_output, validate_config


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("nodes", 0),
        ("domains", 0),
        ("queries", 0),
        ("progress_every", 0),
        ("tenants", 0),
        ("max_edges_per_node", 0),
        ("versions", 0),
    ],
)
def test_validation_rejects_non_positive_values(config, field_name: str, value: int) -> None:
    with pytest.raises(ConfigurationError, match=field_name):
        validate_config(replace(config, **{field_name: value}))


def test_validation_rejects_negative_seed_and_incomplete_domains(config) -> None:
    with pytest.raises(ConfigurationError, match="seed"):
        validate_config(replace(config, seed=-1))
    with pytest.raises(ConfigurationError, match="at least 60"):
        validate_config(replace(config, nodes=59, domains=3))
    with pytest.raises(ConfigurationError, match="tenants"):
        validate_config(replace(config, tenants=3))
    with pytest.raises(ConfigurationError, match="at least 5"):
        validate_config(replace(config, max_edges_per_node=4))


def test_validation_rejects_file_as_output_directory(config, tmp_path: Path) -> None:
    output = tmp_path / "not-a-directory"
    output.write_text("x", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="directory"):
        validate_config(replace(config, output_directory=output))


def test_prepare_output_creates_parent_and_protects_existing_files(config) -> None:
    paths = prepare_output(config)

    assert config.output_directory.parent.is_dir()
    assert not config.output_directory.exists()
    assert tuple(paths) == OUTPUT_FILES
    config.output_directory.mkdir()
    paths["nodes.jsonl"].write_text("existing", encoding="utf-8")

    with pytest.raises(OutputConflictError, match="nodes.jsonl"):
        prepare_output(config)

    overwrite_paths = prepare_output(replace(config, overwrite=True))
    assert overwrite_paths == paths


def test_prepare_output_maps_parent_creation_failure(config, monkeypatch) -> None:
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    with pytest.raises(ConfigurationError, match="parent"):
        prepare_output(config)

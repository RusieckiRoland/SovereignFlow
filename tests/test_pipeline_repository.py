from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sovereignflow.domain import PipelineDefinitionError
from sovereignflow.infrastructure import YamlPipelineRepository


def valid_document() -> dict:
    return {
        "pipeline": {
            "name": "default",
            "entry_step": "start",
            "behavior_version": "1.0",
            "max_steps": 1,
            "steps": [
                {
                    "id": "start",
                    "action": "finalize",
                    "action_version": "1.0",
                    "end": True,
                }
            ],
        }
    }


def write(path: Path, value) -> None:
    path.write_text(yaml.safe_dump(value), encoding="utf-8")


def test_pipeline_repository_loads_deterministic_definition(tmp_path: Path) -> None:
    path = tmp_path / "default.yaml"
    write(path, valid_document())
    repository = YamlPipelineRepository(tmp_path)

    first = repository.load("default")
    second = repository.load("default")

    assert first == second
    assert len(first.checksum) == 64
    assert first.steps[0].terminal is True


def test_pipeline_repository_preserves_action_specific_step_config(tmp_path: Path) -> None:
    raw = valid_document()
    raw["pipeline"]["steps"][0]["prompt_key"] = "general/answer"
    raw["pipeline"]["steps"][0]["user_parts"] = {
        "question": {
            "source": "normalized_query",
            "template": "### User\n{}\n",
        }
    }
    write(tmp_path / "default.yaml", raw)

    step = YamlPipelineRepository(tmp_path).load("default").steps[0]

    assert dict(step.config) == {
        "prompt_key": "general/answer",
        "user_parts": {
            "question": {
                "source": "normalized_query",
                "template": "### User\n{}\n",
            }
        },
    }


def test_pipeline_repository_rejects_path_escape_missing_and_invalid_yaml(tmp_path: Path) -> None:
    repository = YamlPipelineRepository(tmp_path)
    with pytest.raises(PipelineDefinitionError, match="escapes"):
        repository.load("../outside")
    with pytest.raises(PipelineDefinitionError, match="does not exist"):
        repository.load("missing")

    (tmp_path / "broken.yaml").write_text(":", encoding="utf-8")
    with pytest.raises(PipelineDefinitionError, match="Cannot read"):
        repository.load("broken")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: [], "root"),
        (lambda raw: {"other": {}}, "root"),
        (lambda raw: {"pipeline": []}, "mapping"),
        (lambda raw: raw["pipeline"].update(extra=True), "fields"),
        (lambda raw: raw["pipeline"].update(steps={}), "steps must"),
        (lambda raw: raw["pipeline"].update(max_steps="bad"), "integer"),
        (lambda raw: raw["pipeline"].update(max_steps=True), "integer"),
        (lambda raw: raw["pipeline"].update(max_steps=0), "greater than zero"),
        (lambda raw: raw["pipeline"].update(steps=[]), "cannot be empty"),
        (lambda raw: raw["pipeline"].update(name=1), "pipeline.name"),
        (lambda raw: raw["pipeline"].update(steps=["bad"]), "step must"),
        (lambda raw: raw["pipeline"]["steps"][0].update({1: "bad"}), "field names"),
        (lambda raw: raw["pipeline"]["steps"][0].update(id=1), "steps\\[\\].id"),
        (
            lambda raw: (raw["pipeline"]["steps"][0].pop("action_version"), None)[1],
            "Missing",
        ),
        (lambda raw: raw["pipeline"]["steps"][0].update(end="yes"), "boolean"),
        (
            lambda raw: raw["pipeline"]["steps"][0].update(next=1, end=False),
            "next must",
        ),
        (
            lambda raw: raw["pipeline"]["steps"][0].update(
                routes={"selected": 1},
            ),
            "routes must",
        ),
        (
            lambda raw: raw["pipeline"]["steps"][0].update(
                next="second",
                end=True,
            ),
            "terminal",
        ),
    ],
)
def test_pipeline_repository_rejects_invalid_contracts(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    raw = valid_document()
    result = mutate(raw)
    write(tmp_path / "invalid.yaml", result if result is not None else raw)

    with pytest.raises(PipelineDefinitionError, match=message):
        YamlPipelineRepository(tmp_path).load("invalid")

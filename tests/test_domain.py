from sovereignflow.domain import DomainProfile


def test_domain_profile_is_domain_neutral() -> None:
    profile = DomainProfile.from_mapping(
        {
            "name": "customs",
            "description": "Customs assistant",
            "collection": "BtiDecisions",
            "pipeline": "pipelines/default.yaml",
            "system_prompt": "Use evidence.",
            "retrieval": {"mode": "hybrid", "top_k": 5},
        }
    )

    assert profile.name == "customs"
    assert profile.retrieval.top_k == 5
    assert not hasattr(profile, "repository")
    assert not hasattr(profile, "snapshot_id")


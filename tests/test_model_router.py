import pytest

from sovereignflow.providers.llm import (
    ModelEndpoint,
    ModelRoutingPolicy,
    PolicyRoutedModel,
)


class FakeClient:
    def __init__(self, endpoint: ModelEndpoint, result: str) -> None:
        self.endpoint = endpoint
        self.result = result
        self.calls = 0

    def generate(self, **kwargs) -> str:
        self.calls += 1
        return self.result


def endpoint(name: str, scope: str) -> ModelEndpoint:
    return ModelEndpoint(
        name=name,
        scope=scope,
        base_url=f"http://{name}",
        model="test",
    )


def test_local_only_never_calls_external_endpoint() -> None:
    local = FakeClient(endpoint("local", "local"), "local answer")
    external = FakeClient(endpoint("external", "external"), "external answer")
    router = PolicyRoutedModel([external, local], ModelRoutingPolicy.LOCAL_ONLY)

    result = router.generate(system_prompt="system", user_prompt="question")

    assert result == "local answer"
    assert local.calls == 1
    assert external.calls == 0


def test_local_only_fails_without_local_endpoint() -> None:
    external = FakeClient(endpoint("external", "external"), "external answer")
    router = PolicyRoutedModel([external], ModelRoutingPolicy.LOCAL_ONLY)

    with pytest.raises(RuntimeError, match="No model endpoint"):
        router.generate(system_prompt="system", user_prompt="question")


def test_domain_security_context_can_forbid_external_endpoint() -> None:
    local = FakeClient(endpoint("local", "local"), "local answer")
    external = FakeClient(endpoint("external", "external"), "external answer")
    router = PolicyRoutedModel([external, local], ModelRoutingPolicy.EXTERNAL_ALLOWED)

    result = router.generate(
        system_prompt="system",
        user_prompt="question",
        security_context={"allow_external": False},
    )

    assert result == "local answer"
    assert local.calls == 1
    assert external.calls == 0

import pytest

from app.config import (
    graph_organizer_section_timeout_seconds,
    rule_graph_embedding_enabled,
    rule_graph_embedding_model,
    rule_graph_embedding_model_path,
)


def test_graph_organizer_section_timeout_uses_default_and_environment(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GRAPH_ORGANIZER_SECTION_TIMEOUT_SECONDS", raising=False)
    assert graph_organizer_section_timeout_seconds() == 600.0

    monkeypatch.setenv("GRAPH_ORGANIZER_SECTION_TIMEOUT_SECONDS", "12.5")
    assert graph_organizer_section_timeout_seconds() == 12.5


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_graph_organizer_section_timeout_rejects_invalid_values(
    monkeypatch,
    value: str,
) -> None:
    monkeypatch.setenv("GRAPH_ORGANIZER_SECTION_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="GRAPH_ORGANIZER_SECTION_TIMEOUT_SECONDS"):
        graph_organizer_section_timeout_seconds()


def test_rule_graph_embedding_configuration(monkeypatch) -> None:
    monkeypatch.setenv("RULE_GRAPH_EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("RULE_GRAPH_EMBEDDING_MODEL", "test/embedding-model")
    monkeypatch.setenv("RULE_GRAPH_EMBEDDING_MODEL_PATH", "")

    assert rule_graph_embedding_enabled() is True
    assert rule_graph_embedding_model() == "test/embedding-model"
    assert rule_graph_embedding_model_path() is None

    monkeypatch.setenv("RULE_GRAPH_EMBEDDING_ENABLED", "off")
    assert rule_graph_embedding_enabled() is False


def test_rule_graph_embedding_rejects_invalid_toggle(monkeypatch) -> None:
    monkeypatch.setenv("RULE_GRAPH_EMBEDDING_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="RULE_GRAPH_EMBEDDING_ENABLED"):
        rule_graph_embedding_enabled()

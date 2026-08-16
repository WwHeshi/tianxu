import pytest

from app.config import graph_organizer_section_timeout_seconds


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

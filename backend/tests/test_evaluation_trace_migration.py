import importlib.util
from pathlib import Path


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260814_0007_compact_evaluation_traces.py"
    )
    spec = importlib.util.spec_from_file_location("compact_evaluation_traces", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compact_trace_migrates_legacy_snapshot_without_duplicate_requests() -> None:
    migration = _migration_module()
    legacy = {
        "method": "POST",
        "endpoint": "https://example.test/v1/responses",
        "headers": {"Authorization": "Bearer [REDACTED]"},
        "body": {"model": "test", "input": [{"round": 2}]},
        "model_calls": [
            {
                "sequence": 1,
                "stage": "action_selection",
                "request_body": {"model": "test", "input": [{"round": 1}]},
                "response_body": {"output": [{"type": "function_call"}]},
                "duration_ms": 5,
                "status_code": 200,
            },
            {
                "sequence": 2,
                "stage": "final_answer",
                "request_body": {"model": "test", "input": [{"round": 2}]},
                "response_body": {"output_text": '{"answer":"A"}'},
                "duration_ms": 7,
                "status_code": 200,
            },
        ],
        "tool_executions": [{"sequence": 1, "name": "calculate_bazi_chart"}],
    }

    compact = migration._compact_trace(legacy)

    assert compact is not None
    assert compact["initial_request_body"] == {
        "model": "test",
        "input": [{"round": 1}],
    }
    assert compact["model_calls"] == [
        {
            "sequence": 1,
            "stage": "action_selection",
            "response_body": {"output": [{"type": "function_call"}]},
            "duration_ms": 5,
            "tool_call_count": 1,
        },
        {
            "sequence": 2,
            "stage": "final_answer",
            "response_body": {"output_text": '{"answer":"A"}'},
            "duration_ms": 7,
            "tool_call_count": 0,
        },
    ]
    assert "method" not in compact
    assert "headers" not in compact
    assert all("request_body" not in call for call in compact["model_calls"])
    assert all("status_code" not in call for call in compact["model_calls"])


def test_compact_trace_preserves_legacy_top_level_response() -> None:
    migration = _migration_module()

    compact = migration._compact_trace(
        {"body": {"model": "test", "input": "question"}},
        legacy_response={"output_text": '{"answer":"B"}'},
        latency_ms=12,
        item_status="completed",
    )

    assert compact is not None
    assert compact["model_calls"] == [
        {
            "sequence": 1,
            "stage": "final_answer",
            "response_body": {"output_text": '{"answer":"B"}'},
            "duration_ms": 12,
            "tool_call_count": 0,
        }
    ]

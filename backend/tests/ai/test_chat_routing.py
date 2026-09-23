from types import SimpleNamespace

from backend.ai.chat import _chat_route_model, _generate_reply


def test_chat_route_model_prefers_role_override(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.ai.chat.ai_manager",
        SimpleNamespace(
            settings=SimpleNamespace(
                chat_planning_model="planner-model",
                chat_synthesis_model="",
                chat_repair_model="repair-model",
                chat_model="legacy-model",
            )
        ),
    )
    assert _chat_route_model("planning") == "planner-model"
    assert _chat_route_model("repair") == "repair-model"
    assert _chat_route_model("synthesis", _chat_route_model("planning")) == "legacy-model"


def test_ai_off_returns_context_only_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.ai.chat.ai_manager",
        SimpleNamespace(enabled=False, settings=SimpleNamespace()),
    )
    text, grounded, focus = _generate_reply(
        None,
        [
            {
                "symbol": "AAPL",
                "availability": {"engine_warm": True},
                "context": {"price": 220.5, "change_percent": 1.25},
            }
        ],
        [],
        None,
        [],
        "how is AAPL",
        None,
        False,
        ["AAPL"],
        {},
    )
    assert grounded is True
    assert focus == ["AAPL"]
    assert "220.5000" in text
    assert "No narrative or recommendation" in text

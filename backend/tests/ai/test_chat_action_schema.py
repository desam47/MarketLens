"""Guards the invariant that broke silently for 9 tools (get_alerts,
get_sector_data, get_trend, get_confluence, get_relative_strength,
get_tape_state, get_session_stats, get_calendar, import_csv): a tool can be
registered in the default registry and listed in chat.py's
_MARKET_TOOL_ACTIONS, and still be completely unreachable through Chat,
because ChatReplyResponse.action is a strict Pydantic Literal that silently
rejects any value not spelled out in it. Worse, the deterministic
_ALERTS_TOOL_INTENT branch in chat.py actively constructed
ChatReplyResponse(action="get_alerts", ...) and would raise a
ValidationError at runtime every time that phrase matched, since
"get_alerts" was not a valid Literal member. No existing test caught this
because tool-level tests (test_market_tools.py, test_tool_registry.py)
never go through ChatReplyResponse at all.
"""

import typing

from backend.ai.chat import _MARKET_TOOL_ACTIONS
from backend.ai.prompt import ChatReplyResponse
from backend.ai.tool_registry import default_registry


def _valid_action_values() -> set[str]:
    field = ChatReplyResponse.model_fields["action"]
    return set(typing.get_args(field.annotation))


def test_every_market_tool_action_is_a_valid_chat_reply_action():
    valid = _valid_action_values()
    missing = sorted(_MARKET_TOOL_ACTIONS - valid)
    assert not missing, (
        f"chat.py._MARKET_TOOL_ACTIONS contains action(s) ChatReplyResponse.action "
        f"rejects, making them unreachable (and, for any deterministically-constructed "
        f"one, a runtime crash): {missing}"
    )


def test_deterministic_action_construction_does_not_raise():
    """Every _MARKET_TOOL_ACTIONS value must be constructible, not just
    listed -- this is what actually broke for get_alerts."""
    for action in sorted(_MARKET_TOOL_ACTIONS):
        ChatReplyResponse(reply="ok", grounded=True, action=action, action_tool_arguments={})


def test_every_registered_read_only_tool_is_reachable_from_chat():
    """Every read_only tool in the default registry (excluding tools with
    their own dedicated non-generic action_* fields, e.g. calculate) should
    be selectable through chat.py's action set -- otherwise it exists and
    is tested in isolation but Chat itself can never actually call it."""
    dedicated_action_tools = {"calculate", "run_screen"}
    read_only_tools = {
        name for name in default_registry.names()
        if default_registry.get(name).kind == "read_only" and name not in dedicated_action_tools
    }
    unreachable = sorted(read_only_tools - _MARKET_TOOL_ACTIONS)
    assert not unreachable, f"Registered read-only tools Chat can never select: {unreachable}"

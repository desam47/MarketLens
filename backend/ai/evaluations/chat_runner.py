"""Provider-free end-to-end evaluation of Chat routing, tools, and safety.

``runner.py`` scores the answer verifier on canned answer/trace pairs. This
runner drives real conversations through ``answer_chat_message`` /
``stream_chat_message`` — ticker resolution, deterministic and semantic
intent routing, the bounded model planner, confirmation gates, structured
memory, and answer verification all run unmodified. Only the edges are
replaced with scripted fixtures:

- the database is a private in-memory SQLite seeded per case;
- the model returns the case's scripted replies (or raises for an outage);
- registry tools return the case's scripted results (``calculate`` runs for
  real); ``run_screen`` returns a scripted screen;
- symbol context and the market baseline come from the case.

Each turn's ``expect`` block is scored in six categories. A category with
no expectations in a case is not applicable and is not counted, so the
summary never reports a pass for something that was not checked.

Approved regression fixtures exported from the database (see
``export_fixtures.py``) live in ``regression/*.json`` with the same case
shape. Exports start as ``"needs_review": true`` and are skipped until a
maintainer fills in the scripted evidence and expectations.
"""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

CHAT_CASES_PATH = Path(__file__).with_name("phase_5_8_chat_cases.json")
REGRESSION_DIR = Path(__file__).with_name("regression")
CATEGORIES = ("correctness", "tool_choice", "provenance", "clarification", "latency", "safety")
_DEFAULT_MAX_LATENCY_MS = 3000.0
# Budgets are pinned to the shipped defaults so results never depend on a
# machine's .env; a case may override them with "budgets".
_DEFAULT_BUDGETS = {
    "chat_max_tool_calls": 5,
    "chat_max_planning_calls": 2,
    "chat_max_turn_tokens": 60_000,
    "chat_max_chain_steps": None,
    "chat_max_turn_seconds": 30.0,
    "chat_tool_timeout_seconds": 20.0,
}


def load_chat_cases() -> dict[str, Any]:
    return json.loads(CHAT_CASES_PATH.read_text(encoding="utf-8"))


def load_regression_cases() -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(runnable_cases, pending_review_ids)`` from ``regression/``."""
    runnable: list[dict[str, Any]] = []
    pending: list[str] = []
    if not REGRESSION_DIR.is_dir():
        return runnable, pending
    for path in sorted(REGRESSION_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if case.get("needs_review", True):
            pending.append(str(case.get("id") or path.stem))
        else:
            runnable.append(case)
    return runnable, pending


class _ScriptedModel:
    """Stands in for ``ai_manager``: returns one scripted reply per call."""

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.calls = 0
        self.unscripted_calls = 0
        self.enabled = True
        self.settings = MagicMock()
        self.settings.max_tokens = 20000
        self.settings.chat_streaming = False
        self.settings.backtest_tool_enabled = False
        for role in ("chat_model", "chat_planning_model", "chat_synthesis_model", "chat_repair_model"):
            setattr(self.settings, role, "")
        self.is_available = AsyncMock(return_value=True)
        self.complete = AsyncMock(side_effect=self._complete)

    async def _complete(self, *args, **kwargs):
        from backend.ai.provider import AIResponse

        self.calls += 1
        if not self.queue:
            self.unscripted_calls += 1
            reply: Any = {"reply": "(unscripted model call)", "grounded": False}
        else:
            reply = self.queue.pop(0)
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(str(reply["error"]))
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return AIResponse(text=f"```json\n{text}\n```", provider="evaluation", model="scripted")


class _ScriptedTools:
    """Stands in for ``default_registry.execute`` with scripted results."""

    def __init__(self, scripts: dict[str, Any], real_execute) -> None:
        self.scripts = scripts
        self.real_execute = real_execute
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, request):
        from backend.ai.tool_registry import ToolResult

        self.calls.append((request.tool_name, dict(request.arguments)))
        if request.tool_name == "calculate":
            return self.real_execute(request)
        script = self.scripts.get(request.tool_name)
        if script is None:
            return ToolResult(tool_name=request.tool_name, ok=False, error="unscripted tool", failure_kind="invalid")
        if script.get("timeout"):
            return ToolResult(
                tool_name=request.tool_name,
                ok=False,
                error=f"Tool timed out after 20s: {request.tool_name}",
                failure_kind="timeout",
                session=request.session,
                timeframe=request.timeframe,
            )
        return ToolResult(
            tool_name=request.tool_name,
            ok=True,
            data=dict(script.get("data") or {}),
            provider=str(script.get("provider") or "evaluation"),
            source_timestamp=script.get("source_timestamp"),
            freshness_seconds=script.get("freshness_seconds"),
            fallback=bool(script.get("fallback", False)),
            session=request.session,
            timeframe=request.timeframe,
            entitlement="verified",
        )


class _Harness:
    """Owns one case's isolated database and patched edges."""

    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case
        self.stack = ExitStack()
        self.model = _ScriptedModel()
        self.screen_calls: list[str] = []

    def __enter__(self) -> _Harness:
        import backend.models  # noqa: F401 — registers every table on Base
        from backend.ai import chat as chat_module
        from backend.ai.context import InsufficientDataError
        from backend.ai.tool_registry import default_registry
        from backend.config.settings import settings
        from backend.database import Base

        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        chat_module._ctx_cache.clear()

        universe = {str(symbol).upper() for symbol in self.case.get("symbol_universe", [])}
        contexts = {str(k).upper(): v for k, v in (self.case.get("contexts") or {}).items()}
        self.tools = _ScriptedTools(self.case.get("tools") or {}, default_registry.execute)

        def build_context(symbol, *args, **kwargs):
            ctx = contexts.get(str(symbol).upper())
            if ctx is None:
                raise InsufficientDataError(f"no evaluation context for {symbol}")
            wrapper = MagicMock()
            wrapper.compact.return_value = ctx
            return wrapper

        def run_screen(db, parsed):
            self.screen_calls.append(parsed.action_query or "")
            screen = self.case.get("screen") or {}
            return str(screen.get("text", "Screen matched no symbols.")), True, list(screen.get("symbols", []))

        handlers = dict(chat_module._ACTION_HANDLERS)
        handlers["run_screen"] = run_screen
        for target, value in (
            ("backend.repositories.chat_repository.SessionLocal", self.Session),
            ("backend.ai.chat_symbols._known_symbols", lambda: set(universe)),
            ("backend.ai.chat_symbols._validate_unknown", lambda tokens: set()),
            ("backend.ai.chat_symbols._ai_resolve_name", lambda text: []),
            ("backend.ai.chat.build_context", build_context),
            ("backend.ai.chat.build_market_baseline", lambda: dict(self.case.get("baseline") or {})),
            ("backend.ai.chat.ai_manager", self.model),
            ("backend.ai.chat.default_registry.execute", self.tools.execute),
            ("backend.ai.chat._ACTION_HANDLERS", handlers),
            ("backend.ai.chat._kickoff_backfill", lambda symbol: None),
        ):
            self.stack.enter_context(patch(target, value))
        for name, value in {**_DEFAULT_BUDGETS, **(self.case.get("budgets") or {})}.items():
            self.stack.enter_context(patch.object(settings.ai, name, value))
        self.model.enabled = bool(self.case.get("ai_enabled", True))
        self._seed()
        return self

    def __exit__(self, *exc) -> None:
        self.stack.close()
        self.engine.dispose()

    def _seed(self) -> None:
        from backend.models import ChatSession
        from backend.repositories.alert_repository import AlertRepository
        from backend.repositories.watchlist_repository import WatchlistRepository

        seed = self.case.get("seed") or {}
        db = self.Session()
        try:
            watchlists = WatchlistRepository(db)
            for item in seed.get("watchlists", []):
                watchlist = watchlists.create_watchlist(item["name"])
                for symbol in item.get("symbols", []):
                    watchlists.add_symbol_to_watchlist(watchlist.id, symbol)
            alerts = AlertRepository(db)
            for item in seed.get("alerts", []):
                alerts.create(item["name"], item["symbol"], item["condition_type"], str(item["threshold"]))
            session = ChatSession(symbol="*", scope="universal")
            db.add(session)
            db.commit()
            self.session_id = session.id
        finally:
            db.close()

    def db_state(self) -> dict[str, Any]:
        from backend.models import Alert, ChatSession
        from backend.repositories.watchlist_repository import WatchlistRepository

        db = self.Session()
        try:
            repo = WatchlistRepository(db)
            watchlists = {
                wl.name: sorted(s.symbol for s in repo.get_watchlist_symbols(wl.id))
                for wl in repo.get_watchlists()
            }
            session = db.get(ChatSession, self.session_id)
            planner_state = json.loads(session.planner_state or "{}") if session else {}
            return {
                "alerts": db.query(Alert).count(),
                "watchlists": watchlists,
                "pending_confirmation": (planner_state or {}).get("pending_confirmation"),
                "memory": planner_state or {},
            }
        finally:
            db.close()

    def run_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        from backend.ai.chat import answer_chat_message, stream_chat_message

        self.model.queue = list(turn.get("model", []))
        model_calls_before = self.model.calls
        tool_calls_before = len(self.tools.calls)
        screens_before = len(self.screen_calls)
        started = time.perf_counter()
        deltas: list[str] = []
        if turn.get("transport", self.case.get("transport", "blocking")) == "stream":
            final = None
            for kind, payload in stream_chat_message(self.session_id, turn["user"]):
                if kind == "delta":
                    deltas.append(payload)
                elif kind == "final":
                    final = payload
            assert final is not None, "stream ended without a final event"
            message, grounded, focus, *_ = final
        else:
            message, grounded, focus, *_ = answer_chat_message(self.session_id, turn["user"])
        elapsed_ms = (time.perf_counter() - started) * 1000
        trace = list(getattr(message, "planner_trace", []) or [])
        blocks = list(getattr(message, "response_blocks_payload", []) or [])
        verification = next((b.get("data", {}) for b in blocks if b.get("type") == "verification"), {})
        return {
            "content": message.content,
            "grounded": grounded,
            "focus": list(focus),
            "steps": [item for item in trace if item.get("kind") == "step"],
            "tool_calls": self.tools.calls[tool_calls_before:],
            "screens": self.screen_calls[screens_before:],
            "model_calls": self.model.calls - model_calls_before,
            "unscripted_model_calls": self.model.unscripted_calls,
            "verification": verification,
            "evidence_refs": list(verification.get("evidence_refs") or []),
            "failed_tools": [
                item for item in trace
                if item.get("ok") is False and item.get("tool") and item.get("kind") != "step"
            ],
            "elapsed_ms": elapsed_ms,
            "deltas": deltas,
            "db": self.db_state(),
        }


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() == actual.lower()
    return expected == actual


def _score_turn(expect: dict[str, Any], observed: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    """Return ``(scores, failures)``; scores hold applicable categories only."""
    checks: dict[str, list[tuple[bool, str]]] = {category: [] for category in CATEGORIES}
    content = observed["content"]
    executed = [step.get("tool") for step in observed["steps"] if step.get("tool") != "planning"]

    # Tool choice: which actions ran, with what arguments, and how many model calls.
    if "tools" in expect:
        checks["tool_choice"].append((executed == expect["tools"], f"tools {executed} != {expect['tools']}"))
    for tool, arguments in (expect.get("tool_arguments") or {}).items():
        calls = [args for name, args in observed["tool_calls"] if name == tool]
        checks["tool_choice"].append((
            any(_subset(arguments, args) for args in calls),
            f"{tool} not called with {arguments}; calls {calls}",
        ))
    if "max_model_calls" in expect:
        checks["tool_choice"].append((
            observed["model_calls"] <= expect["max_model_calls"],
            f"model calls {observed['model_calls']} > {expect['max_model_calls']}",
        ))
    if "screened" in expect:
        checks["tool_choice"].append((bool(observed["screens"]) == expect["screened"], f"screen calls {observed['screens']}"))

    # Clarification: a question back, and nothing executed.
    if "clarification" in expect:
        asked = "?" in content and not any(s.get("status") == "completed" for s in observed["steps"])
        checks["clarification"].append((asked == expect["clarification"], f"clarification={asked}: {content!r}"))

    # Correctness of what the trader sees.
    for needle in expect.get("reply_contains", []):
        checks["correctness"].append((needle.lower() in content.lower(), f"reply missing {needle!r}: {content!r}"))
    for needle in expect.get("reply_not_contains", []):
        checks["correctness"].append((needle.lower() not in content.lower(), f"reply contains {needle!r}"))
    if "focus" in expect:
        checks["correctness"].append((observed["focus"] == expect["focus"], f"focus {observed['focus']} != {expect['focus']}"))
    if "verification" in expect:
        status = observed["verification"].get("status")
        checks["correctness"].append((status == expect["verification"], f"verification {status} != {expect['verification']}"))
    if "grounded" in expect:
        checks["correctness"].append((observed["grounded"] == expect["grounded"], f"grounded {observed['grounded']}"))
    for key, value in (expect.get("memory") or {}).items():
        actual = observed["db"]["memory"].get(key)
        checks["correctness"].append((_subset(value, actual), f"memory {key}={actual!r} != {value!r}"))
    if "failure_kind" in expect:
        kinds = [item.get("failure_kind") for item in observed["failed_tools"]]
        checks["correctness"].append((expect["failure_kind"] in kinds, f"failure kinds {kinds}"))

    # Provenance: evidence IDs must back a data answer.
    if expect.get("requires_evidence"):
        checks["provenance"].append((bool(observed["evidence_refs"]), "no evidence references"))

    # Safety: side effects and confirmation gates.
    for tool, status in (expect.get("step_status") or {}).items():
        statuses = [s.get("status") for s in observed["steps"] if s.get("tool") == tool]
        checks["safety"].append((status in statuses, f"{tool} step statuses {statuses} lack {status}"))
    db_expect = expect.get("db") or {}
    if "alerts" in db_expect:
        checks["safety"].append((observed["db"]["alerts"] == db_expect["alerts"], f"alerts {observed['db']['alerts']}"))
    for name, symbols in (db_expect.get("watchlists") or {}).items():
        actual = observed["db"]["watchlists"].get(name)
        wanted = None if symbols is None else sorted(symbols)
        checks["safety"].append((actual == wanted, f"watchlist {name}={actual} != {wanted}"))
    if "pending_confirmation" in db_expect:
        pending = observed["db"]["pending_confirmation"]
        ok = (pending is None) if db_expect["pending_confirmation"] is None else _subset(db_expect["pending_confirmation"], pending)
        checks["safety"].append((ok, f"pending_confirmation={pending}"))

    # Latency: provider-free turns must stay within the turn budget.
    max_latency = float(expect.get("max_latency_ms", _DEFAULT_MAX_LATENCY_MS))
    checks["latency"].append((observed["elapsed_ms"] <= max_latency, f"{observed['elapsed_ms']:.0f} ms > {max_latency:.0f} ms"))

    scores = {category: all(ok for ok, _ in items) for category, items in checks.items() if items}
    failures = [message for items in checks.values() for ok, message in items if not ok]
    return scores, failures


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, bool] = {}
    failures: list[str] = []
    with _Harness(case) as harness:
        for index, turn in enumerate(case["turns"], start=1):
            observed = harness.run_turn(turn)
            turn_scores, turn_failures = _score_turn(turn.get("expect") or {}, observed)
            for category, ok in turn_scores.items():
                scores[category] = scores.get(category, True) and ok
            failures.extend(f"turn {index}: {message}" for message in turn_failures)
            if observed["unscripted_model_calls"] and not turn.get("allow_unscripted_model"):
                scores["tool_choice"] = False
                failures.append(f"turn {index}: unscripted model call")
    return {
        "id": case["id"],
        "category": case.get("category"),
        "scores": scores,
        "failures": failures,
        "passed": not failures,
    }


def run_chat_cases(cases: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if cases is None:
        regression, _ = load_regression_cases()
        cases = [*load_chat_cases()["cases"], *regression]
    return [run_case(case) for case in cases]


def chat_score_summary(results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = results if results is not None else run_chat_cases()
    _, pending = load_regression_cases()
    return {
        "version": load_chat_cases()["version"],
        "case_count": len(rows),
        "passed_cases": sum(1 for row in rows if row["passed"]),
        "categories": {
            category: {
                "passed": sum(1 for row in rows if row["scores"].get(category) is True),
                "total": sum(1 for row in rows if category in row["scores"]),
            }
            for category in CATEGORIES
        },
        "regression_pending_review": pending,
    }


def main() -> int:
    results = run_chat_cases()
    for row in results:
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"{mark} {row['id']}")
        for failure in row["failures"]:
            print(f"     {failure}")
    print(json.dumps(chat_score_summary(results), indent=2))
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHAT_CASES_PATH",
    "REGRESSION_DIR",
    "chat_score_summary",
    "load_chat_cases",
    "load_regression_cases",
    "run_case",
    "run_chat_cases",
]

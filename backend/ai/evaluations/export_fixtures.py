"""Export approved Chat regression fixtures into evaluation case drafts.

Promoting feedback (``POST /api/ai/chat/messages/{id}/regression-fixture``)
stores the failed question and answer in ``chat_regression_fixtures``. This
command turns each stored fixture into ``regression/fixture_<id>.json`` in
the ``chat_runner`` case shape so it can become a permanent regression test.

A draft is written with ``"needs_review": true`` and is skipped by the
runner until a maintainer scripts the evidence the turn needs (contexts,
tool results, model replies), writes the expectations that define a correct
answer, and flips ``needs_review`` to false. The only pre-filled expectation
is that the known-bad answer is not reproduced verbatim. Existing files are
never overwritten, so reviewed cases are safe to keep in source control.

The drafts contain the trader's question, the bad answer, and the feedback
comment; review them for private details before committing.

    python -m backend.ai.evaluations.export_fixtures [--limit 100]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.ai.evaluations.chat_runner import REGRESSION_DIR


def _symbols_from_blocks(raw_blocks: str | None) -> list[str]:
    try:
        blocks = json.loads(raw_blocks or "[]")
    except (TypeError, ValueError):
        return []
    symbols: set[str] = set()
    for block in blocks if isinstance(blocks, list) else []:
        data = block.get("data") if isinstance(block, dict) else None
        groups = data.get("symbols") if isinstance(data, dict) else None
        if isinstance(groups, dict):
            for values in groups.values():
                symbols.update(str(value).upper() for value in values or [] if isinstance(value, str))
    return sorted(symbols)


def fixture_to_case(fixture: Any) -> dict[str, Any]:
    """Draft one runner case from a stored ``ChatRegressionFixture``."""
    return {
        "id": f"fixture_{fixture.id}",
        "category": "regression",
        "needs_review": True,
        "source": {
            "fixture_id": fixture.id,
            "message_id": fixture.message_id,
            "rating": fixture.rating,
            "feedback_category": fixture.category,
            "comment": fixture.comment,
            "bad_response": fixture.response,
        },
        "review_notes": (
            "Script the evidence this question needs (symbol_universe, contexts, tools, "
            "and any model replies), add expectations that define a correct answer "
            "(tools, reply_contains, verification, db, ...), then set needs_review to false."
        ),
        "symbol_universe": _symbols_from_blocks(fixture.response_blocks),
        "contexts": {},
        "tools": {},
        "turns": [
            {
                "user": fixture.prompt,
                "model": [],
                "expect": {"reply_not_contains": [fixture.response]},
            }
        ],
    }


def export_fixtures(limit: int = 100, directory: Path = REGRESSION_DIR) -> list[Path]:
    """Write drafts for fixtures that have no case file yet; return new paths."""
    from backend.repositories.chat_repository import ChatRepository

    directory.mkdir(parents=True, exist_ok=True)
    repo = ChatRepository()
    written: list[Path] = []
    try:
        for fixture in repo.get_regression_fixtures(limit=limit):
            if fixture.status != "approved":
                continue
            path = directory / f"fixture_{fixture.id}.json"
            if path.exists():
                continue
            path.write_text(json.dumps(fixture_to_case(fixture), indent=2) + "\n", encoding="utf-8")
            written.append(path)
    finally:
        repo.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    written = export_fixtures(limit=args.limit)
    for path in written:
        print(f"wrote {path}")
    print(f"{len(written)} new draft(s); review them before setting needs_review to false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

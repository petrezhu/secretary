"""Rule-based candidate mining for the ColdSkill sedimentation pipeline.

Pure functions only: every input is a parameter (paths, thresholds,
windows, "now") and every output is returned or written to a file.
Zero LLM calls, zero network access, zero dependence on daemon classes.

Input contract (``queries.jsonl``, appended by
:mod:`secretary.gateway.inbound`): one JSON object per line:

    {"ts": <epoch seconds>, "text": "<original, first 80 chars>", "outcome": <str>}

``outcome`` is "truncated", a hit intent-handler name, or "allow"
(deferred to the agent). Only rows whose outcome is exactly "allow"
may contribute to a candidate.

Candidate rule (spec "候选挖掘"):

    An exact original text that appeared ≥ min_count times within the
    last data_span_days days, where EVERY one of its in-span rows has
    outcome "allow", and whose most recent appearance (last_seen)
    falls inside the last window_days days.

Time-window semantics (both boundaries inclusive on the "last N days"
edge, deterministic and unit-tested):

    - data span:   now - data_span_days*86400 <= ts <= now
    - window:      now - window_days*86400 <= last_seen <= now

Rows with future timestamps (clock skew) are outside the span and are
ignored. Tolerated garbage — missing file, empty file, malformed JSON
lines, wrong-typed fields — is silently skipped, never raised; the
rotation policy upstream means the file is treated as a snapshot of
whatever is currently present.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DAY_SECONDS = 86400

# The only adoption mode mined in this stage; later stages may refine it.
SUGGESTED_ACTION = "literal"


def _coerce_ts(now: float | int | datetime) -> float:
    """Accept ``now`` as an epoch number or a datetime; return epoch seconds."""
    if isinstance(now, datetime):
        return now.timestamp()
    return float(now)


def _iter_rows(queries_path: str | os.PathLike | Path) -> Iterator[tuple[float, str, str]]:
    """Yield valid (ts, text, outcome) triples, silently skipping garbage."""
    path = Path(queries_path)
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ts = obj.get("ts")
                text = obj.get("text")
                outcome = obj.get("outcome")
                if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                    continue
                if not isinstance(text, str) or not text:
                    continue
                if not isinstance(outcome, str) or not outcome:
                    continue
                yield (float(ts), text, outcome)
    except OSError:
        return


def aggregate_queries(
    queries_path: str | os.PathLike | Path,
    now: float | int | datetime,
    min_count: int = 3,
    window_days: int = 7,
    data_span_days: int = 30,
) -> list[dict]:
    """Aggregate ``queries.jsonl`` into candidate rule entries.

    Rules (all thresholds/windows parameterised):
      - only rows whose ``ts`` falls inside the last ``data_span_days``
        days are considered;
      - rows are grouped by exact ``text``;
      - a group becomes a candidate iff its row count >= ``min_count``,
        every one of its in-span rows has outcome "allow", and its most
        recent ``ts`` falls inside the last ``window_days`` days.

    Returns a list of ``{"text", "count", "last_seen", "suggested_action"}``
    dicts sorted by count (desc), then text — deterministic.
    """
    now_ts = _coerce_ts(now)
    span_cutoff = now_ts - data_span_days * DAY_SECONDS
    window_cutoff = now_ts - window_days * DAY_SECONDS

    groups: dict[str, dict] = {}
    for ts, text, outcome in _iter_rows(queries_path):
        if not span_cutoff <= ts <= now_ts:
            continue
        group = groups.setdefault(
            text, {"count": 0, "all_allow": True, "last_ts": float("-inf")}
        )
        group["count"] += 1
        if outcome != "allow":
            group["all_allow"] = False
        if ts > group["last_ts"]:
            group["last_ts"] = ts

    candidates = [
        {
            "text": text,
            "count": group["count"],
            "last_seen": datetime.fromtimestamp(group["last_ts"], tz=timezone.utc).isoformat(),
            "suggested_action": SUGGESTED_ACTION,
        }
        for text, group in groups.items()
        if group["count"] >= min_count
        and group["all_allow"]
        and group["last_ts"] >= window_cutoff
    ]
    candidates.sort(key=lambda c: (-c["count"], c["text"]))
    return candidates


def write_candidates(
    candidates: list[dict],
    out_path: str | os.PathLike | Path,
    generated_at: str | None = None,
) -> dict:
    """Write the candidate file as ``{"generated_at": iso, "candidates": [...]}``.

    Always writes — an empty candidate list still refreshes the file and
    ``generated_at``, so downstream consumers can tell a clean sweep from
    a stale file. ``generated_at`` defaults to the current UTC time; pass
    an ISO string for deterministic output (tests).
    """
    payload = {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "candidates": list(candidates),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def queries_path() -> Path:
    """Location of the inbound query trace, per the gateway's convention."""
    data_dir = os.environ.get("SECRETARY_DATA_DIR", "")
    return Path(os.path.join(data_dir, "queries.jsonl") if data_dir else "/tmp/queries.jsonl")


def candidates_path() -> Path:
    """Location of the aggregated candidate output (rule_candidates.json)."""
    data_dir = os.environ.get("SECRETARY_DATA_DIR", "")
    return Path(
        os.path.join(data_dir, "rule_candidates.json") if data_dir else "/tmp/rule_candidates.json"
    )
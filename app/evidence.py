"""Evidence ledger: every tool result gets an ID (E1, E2, ...) with its data and provenance.

The agent must cite evidence IDs, and the grounding verifier checks that each figure in an
answer can be traced back to a value in the ledger.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Evidence:
    id: str
    tool: str
    data: dict[str, Any]
    sources: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)

    def for_llm(self) -> dict[str, Any]:
        return {"evidence_id": self.id, "data": self.data, "notes": self.notes,
                "sources": [{k: s.get(k) for k in ("name", "retrieved_at", "stale")} for s in self.sources]}

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "tool": self.tool, "sources": self.sources, "notes": self.notes}


class EvidenceLedger:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._counter = itertools.count(1)

    def add(self, tool: str, data: dict[str, Any], sources: list[dict[str, Any]], notes: list[str] | None = None) -> Evidence:
        ev = Evidence(f"E{next(self._counter)}", tool, data, sources, list(notes or []))
        self._items[ev.id] = ev
        return ev

    def __contains__(self, evidence_id: str) -> bool:
        return evidence_id in self._items

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def numbers(self, evidence_ids: list[str] | None = None) -> list[float]:
        """Every numeric value in the ledger, including numbers inside strings (dates, times, ranges)."""
        items = self._items.values() if evidence_ids is None else [self._items[i] for i in evidence_ids if i in self._items]
        out: list[float] = []
        for ev in items:
            _collect_numbers(ev.data, out)
        return out


def _collect_numbers(value: Any, out: list[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, str):
        out.extend(float(m) for m in _NUM.findall(value))
        out.extend(float(m) for m in re.findall(r"\d+", value))  # parts of dates like 2026-11-03
    elif isinstance(value, dict):
        for v in value.values():
            _collect_numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect_numbers(v, out)

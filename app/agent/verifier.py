"""Grounding verifier: checks that every data figure in an answer traces back to tool evidence.

A "data figure" is a number attached to a measurement unit or currency (mm, °C, %, km/h, m³/s,
₹, quintal, kg, L, AQI ...). Each must match a value the agent actually received from a tool
(within rounding tolerance). Other numbers (dates, counts, list numbering) are reported but do
not block the answer. Citations like [E3] must refer to real evidence IDs. The check is
language-agnostic: Indic digits are normalised first and units are matched as symbols.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..evidence import EvidenceLedger, _collect_numbers

_INDIC_ZEROS = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66]  # Deva, Beng, Guru, Gujr, Orya, Taml, Telu, Knda, Mlym
DIGIT_MAP = {zero + i: str(i) for zero in _INDIC_ZEROS for i in range(10)}

NUMBER = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
NUM_RE = re.compile(rf"(?<![\w.])(?:{NUMBER})(?![\w])")
CITE_RE = re.compile(r"\[\s*(E\d+(?:\s*[,;]\s*E\d+)*)\s*\]")
UNIT_AFTER = re.compile(
    r"^\s?(?:mm|cm|°\s?c|℃|°|%|km/?h|kmph|kph|m3/s|m³/s|cumecs|quintals?|qtl|q\b|/q|/quintal|kg|litres?|liters?|l\b|"
    r"µg/m³|ug/m3|aqi|degrees?|डिग्री|मिमी|लीटर|क्विंटल|प्रतिशत)",
    re.IGNORECASE)
CURRENCY_BEFORE = re.compile(r"(?:₹|rs\.?|inr|रु\.?|रुपये)\s?$", re.IGNORECASE)
RANGE_THEN_UNIT = re.compile(rf"^\s?(?:-|–|to|से)\s?(?:{NUMBER})\s?(?:mm|°\s?c|℃|%|km/?h|m³/s|m3/s|kg|l\b)", re.IGNORECASE)


@dataclass
class VerificationReport:
    data_figures: int = 0
    supported: int = 0
    unsupported: list[str] = field(default_factory=list)
    other_numbers_unmatched: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    unknown_citations: list[str] = field(default_factory=list)

    @property
    def missing_citations(self) -> bool:
        return self.data_figures > 0 and not self.citations

    @property
    def ok(self) -> bool:
        return not self.unsupported and not self.unknown_citations and not self.missing_citations

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data_figures": self.data_figures, "supported": self.supported,
                "unsupported": self.unsupported, "other_numbers_unmatched": self.other_numbers_unmatched,
                "citations": self.citations, "unknown_citations": self.unknown_citations,
                "missing_citations": self.missing_citations}


def _matches(value: float, pool: list[float]) -> bool:
    target = abs(value)
    for e in pool:
        ref = abs(e)
        if abs(target - ref) <= max(0.51, 0.025 * ref):
            return True
    return False


def verify(answer: str, ledger: EvidenceLedger, context_texts: list[str] | None = None) -> VerificationReport:
    text = answer.translate(DIGIT_MAP)
    report = VerificationReport()

    for group in CITE_RE.findall(text):
        for cid in re.split(r"\s*[,;]\s*", group):
            if cid not in report.citations:
                report.citations.append(cid)
    report.unknown_citations = [c for c in report.citations if c not in ledger]

    pool = [n for ev in ledger.all() for n in _numbers_seen_by_model(ev)]
    for ctx in context_texts or []:
        _collect_numbers(ctx.translate(DIGIT_MAP), pool)

    body = CITE_RE.sub(" ", text)
    for m in NUM_RE.finditer(body):
        raw = m.group(0)
        value = float(raw.replace(",", ""))
        after, before = body[m.end():m.end() + 14], body[max(0, m.start() - 8):m.start()]
        unit, currency = UNIT_AFTER.match(after), CURRENCY_BEFORE.search(before)
        is_data = bool(unit or currency or RANGE_THEN_UNIT.match(after))
        if not is_data and value <= 10 and "." not in raw:
            continue  # small counts, list numbering, "2-3 days"
        supported = _matches(value, pool)
        if is_data:
            report.data_figures += 1
            label = f"{currency.group(0) if currency else ''}{raw}{unit.group(0) if unit else ''}".strip()
            if supported:
                report.supported += 1
            elif label not in report.unsupported:
                report.unsupported.append(label)
        elif not supported and raw not in report.other_numbers_unmatched:
            report.other_numbers_unmatched.append(raw)
    return report


def _numbers_seen_by_model(ev: Any) -> list[float]:
    out: list[float] = []
    _collect_numbers(ev.for_llm(), out)
    return out

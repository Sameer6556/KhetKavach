from app.agent.verifier import verify
from app.evidence import EvidenceLedger


def ledger():
    led = EvidenceLedger()
    led.add("get_weather_outlook", {"summary": {"rain_total_mm": 72.4, "max_tmax_c": 33.1},
                                    "hazards": [{"date": "2026-11-03", "hazard": "heavy"}]}, [])
    led.add("get_mandi_prices", {"best_markets": [{"market": "Mumbai", "modal_price": 2600}], "best_vs_local_gap": {"amount": 350, "pct": 16}}, [])
    return led


def test_grounded_answer_passes():
    text = "Expect 72 mm of rain [E1], with highs near 33.1 °C [E1]. Mumbai pays ₹2,600/quintal, ₹350 more (16%) [E2]."
    r = verify(text, ledger())
    assert r.ok, r.to_dict()
    assert r.data_figures == 5 and r.supported == 5


def test_unsupported_figure_is_caught():
    r = verify("Expect 120 mm of rain on 3 Nov [E1].", ledger())
    assert not r.ok
    assert r.unsupported == ["120 mm"]


def test_unknown_citation_and_missing_citations():
    assert verify("72 mm of rain [E9].", ledger()).unknown_citations == ["E9"]
    assert verify("72 mm of rain is expected.", ledger()).missing_citations


def test_indic_digits_and_hindi_units_are_normalised():
    text = "अगले हफ्ते ७२ mm बारिश [E1]; मंडी भाव ₹२६०० [E2]।"
    r = verify(text, ledger())
    assert r.ok, r.to_dict()
    assert r.data_figures == 2


def test_counts_and_dates_do_not_block():
    r = verify("Spray on 2 days this week, best on 3 November 2026 [E1].", ledger())
    assert r.ok
    assert r.data_figures == 0


def test_ranges_with_units_are_checked():
    r = verify("Temperatures between 33-40 °C [E1].", ledger())
    assert "40 °C" in r.unsupported or "40 °c" in [u.lower() for u in r.unsupported]

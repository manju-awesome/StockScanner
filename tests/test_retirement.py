"""
Tests for the retirement engine.

These target the claims the engine makes about itself rather than the
arithmetic, because the arithmetic is the easy part. The claims are:
missing input stays missing, spending flexibility is load-bearing,
sequence risk shows up as a gap between the deterministic and simulated
answers, and the score never quietly counts an unknown as a zero.
"""

import json

import pytest

from stockanalysis.core.retirement import (assumptions as A, balance_sheet,
                                           engine, fi, goals, mix,
                                           montecarlo, profile as profile_mod,
                                           projection, scenarios,
                                           score as score_mod, store,
                                           withdrawal)


BASE = {
    "people": [{"name": "You", "age": 40, "role": "self"}],
    "retirement_age": 58,
    "income": {"salary": 200000},
    "savings": {"401k": 40000},
    "spending": {"essential": {"housing": 45000, "food": 15000,
                               "healthcare": 8000, "taxes": 42000},
                 "lifestyle": {"travel": 15000, "restaurants": 8000}},
    "retirement_spending": {"essential": 70000, "lifestyle": 30000},
    "assets": [{"kind": "401k", "value": 500000},
               {"kind": "roth_ira", "value": 100000},
               {"kind": "cash", "value": 60000, "goal": "emergency"}],
    "liabilities": [],
    "one_offs": [],
    "allocation": {"equity": 0.8, "bond": 0.15, "cash": 0.05},
}


def build(**overrides):
    raw = json.loads(json.dumps(BASE))
    raw.update(overrides)
    return engine.build(raw, link_brokerage=False)


# ── missing means missing ────────────────────────────────────────────────

def test_absent_asset_value_is_not_counted_as_zero():
    p = profile_mod.load({"assets": [{"kind": "401k", "value": 500000},
                                     {"kind": "hsa"}]})
    b = balance_sheet.build(p, 5.0, 3.0, link_brokerage=False)
    assert b["total_assets"] == 500000
    assert b["unknown_count"] == 1


def test_missing_inputs_are_named_not_filled():
    snap = engine.build({"people": [{"age": 40, "role": "self"}]},
                        run_monte_carlo=False, run_scenarios=False,
                        link_brokerage=False)
    assert snap["target_portfolio"] is None
    assert "Annual spending" in snap["missing"]
    assert snap["completeness_pct"] < 50


def test_blank_string_is_missing_not_zero():
    p = profile_mod.load({"assets": [{"kind": "cash", "value": ""}]})
    b = balance_sheet.build(p, 5.0, 3.0, link_brokerage=False)
    assert b["unknown_count"] == 1
    assert b["total_assets"] == 0


# ── the spending split does work ─────────────────────────────────────────

def test_more_flexible_spending_lowers_the_required_portfolio():
    rigid = withdrawal.all_models(100000, 20000, 34, 5.0)
    flexible = withdrawal.all_models(60000, 60000, 34, 5.0)
    assert flexible["B"]["required"] < rigid["B"]["required"]


def test_guardrails_flagged_unusable_when_there_is_nothing_to_cut():
    m = withdrawal.model_d_guardrails(115000, 5000)
    assert not m["viable"]
    assert withdrawal.all_models(115000, 5000, 34, 5.0)["guardrail_warning"]


def test_flexibility_raises_simulated_survival():
    p = profile_mod.load(BASE)
    a = A.resolve({})
    cmp_ = montecarlo.compare_flexibility(
        p, a, retirement_age=55, current_portfolio=600000,
        annual_savings=40000, allocation=BASE["allocation"])
    assert cmp_["gain_pts"] > 0


# ── sequence risk is visible ─────────────────────────────────────────────

def test_simulated_survival_is_worse_than_the_straight_line_path():
    """The whole reason the Monte Carlo panel exists.

    A deterministic path that survives on average returns is routinely a
    coin flip once the returns arrive in a real order. If this ever
    stops being true the simulation has lost its volatility.
    """
    p = profile_mod.load(BASE)
    a = A.resolve({})
    path = projection.run(p, a, retirement_age=58,
                          current_portfolio=600000, annual_savings=40000,
                          allocation=BASE["allocation"])
    mc = montecarlo.simulate(p, a, retirement_age=58,
                             current_portfolio=600000, annual_savings=40000,
                             allocation=BASE["allocation"], paths=2000)
    assert path["survives"]
    assert mc["success_pct"] < 100


def test_probability_based_age_is_never_younger_than_deterministic():
    p = profile_mod.load(BASE)
    a = A.resolve({})
    det = projection.earliest_safe_age(
        p, a, current_portfolio=600000, annual_savings=40000,
        allocation=BASE["allocation"])
    prob = montecarlo.age_at_probability(
        p, a, current_portfolio=600000, annual_savings=40000,
        allocation=BASE["allocation"], target_prob=85.0, search_paths=1500)
    assert det["earliest_age"] is not None
    assert prob["age"] >= det["earliest_age"]


def test_simulation_is_reproducible():
    p = profile_mod.load(BASE)
    a = A.resolve({})
    kw = dict(retirement_age=58, current_portfolio=600000,
              annual_savings=40000, allocation=BASE["allocation"],
              paths=1500)
    assert (montecarlo.simulate(p, a, **kw)["success_pct"]
            == montecarlo.simulate(p, a, **kw)["success_pct"])


# ── one-offs land where they are dated ───────────────────────────────────

def test_one_off_hits_its_own_year_and_only_that_year():
    raw = dict(BASE)
    raw = json.loads(json.dumps(BASE))
    raw["one_offs"] = [{"label": "College", "amount": 200000,
                        "year": 2030, "funded_from": "portfolio"}]
    p = profile_mod.load(raw)
    a = A.resolve({})
    path = projection.run(p, a, retirement_age=58,
                          current_portfolio=600000, annual_savings=40000,
                          allocation=raw["allocation"], start_year=2026)
    hits = [r for r in path["rows"] if r["one_off"]]
    assert len(hits) == 1
    assert hits[0]["year"] == 2030 and hits[0]["one_off"] == 200000


def test_one_off_funded_elsewhere_does_not_touch_the_portfolio():
    raw = json.loads(json.dumps(BASE))
    raw["one_offs"] = [{"label": "College", "amount": 200000,
                        "year": 2030, "funded_from": "dedicated"}]
    p = profile_mod.load(raw)
    a = A.resolve({})
    path = projection.run(p, a, retirement_age=58,
                          current_portfolio=600000, annual_savings=40000,
                          allocation=raw["allocation"], start_year=2026)
    assert path["one_off_total"] == 0


def test_earmarked_capital_is_removed_from_retirement_assets():
    p = profile_mod.load({"assets": [
        {"kind": "brokerage", "value": 400000},
        {"kind": "cash", "value": 100000, "goal": "education"}]})
    b = balance_sheet.build(p, 5.0, 3.0, link_brokerage=False)
    assert b["investable"] == 500000
    assert b["retirement_assets"] == 400000


def test_the_home_is_net_worth_but_not_investable():
    p = profile_mod.load({"assets": [
        {"kind": "primary_residence", "value": 900000},
        {"kind": "brokerage", "value": 100000}]})
    b = balance_sheet.build(p, 5.0, 3.0, link_brokerage=False)
    assert b["net_worth"] == 1000000
    assert b["investable"] == 100000


# ── the score does not punish silence ────────────────────────────────────

def test_unscored_components_leave_the_denominator():
    snap = build()
    sc = snap["score"]
    assert sc["coverage_pct"] < 100
    assert "Healthcare preparation" in sc["unscored"]
    scored = [c for c in sc["components"] if c["scored"]]
    expected = (sum(c["score"] * c["weight"] for c in scored)
                / sum(c["weight"] for c in scored))
    assert abs(sc["score"] - expected) < 0.05


def test_healthcare_data_moves_the_score_and_the_coverage():
    without = build()
    with_hc = build(healthcare={"covered_until_medicare": True,
                                "reserve": 150000,
                                "long_term_care": "policy in force"})
    assert with_hc["score"]["coverage_pct"] > without["score"]["coverage_pct"]
    assert "Healthcare preparation" not in with_hc["score"]["unscored"]


def test_weights_sum_to_one_hundred():
    assert sum(score_mod.WEIGHTS.values()) == 100


# ── debt ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate,expected", [
    (18.0, "PAY DOWN"),      # credit card: nothing beats it
    (9.0, "PAY DOWN"),
    (3.0, "MAINTAIN"),       # cheap fixed debt with a real spread
])
def test_debt_disposition_bands(rate, expected):
    d = balance_sheet.debt_disposition({"rate": rate}, 5.0, 3.0)
    assert d["action"].startswith(expected)


def test_debt_with_no_rate_is_reviewed_not_guessed():
    assert balance_sheet.debt_disposition({}, 5.0, 3.0)["action"] == "REVIEW"


# ── emergency fund ───────────────────────────────────────────────────────

def test_emergency_fund_is_sized_on_essentials_not_total_spending():
    p = profile_mod.load(BASE)
    b = balance_sheet.build(p, 5.0, 3.0, link_brokerage=False)
    ef = goals.emergency_fund(p, b)
    monthly_total = p.total_spending / 12
    assert ef["monthly_essential"] < monthly_total
    assert ef["minimum"] == pytest.approx(ef["monthly_essential"] * 3)


def test_planned_relocation_increases_the_reserve():
    raw = json.loads(json.dumps(BASE))
    raw["current_country"], raw["retirement_country"] = "US", "India"
    p = profile_mod.load(raw)
    b = balance_sheet.build(p, 5.0, 3.0, link_brokerage=False)
    home = goals.emergency_fund(profile_mod.load(BASE), b)
    away = goals.emergency_fund(p, b)
    assert away["months_recommended"] > home["months_recommended"]


# ── allocation drives return, not the other way round ────────────────────

def test_expected_return_falls_as_bonds_rise():
    a = A.resolve({})
    aggressive = mix.expected_real_return({"equity": 1.0}, a)
    balanced = mix.expected_real_return({"equity": 0.5, "bond": 0.5}, a)
    assert aggressive > balanced


def test_diversification_lowers_volatility_below_the_weighted_average():
    a = A.resolve({})
    alloc = {"equity": 0.6, "bond": 0.4}
    naive = (0.6 * A.value(a, "equity_volatility")
             + 0.4 * A.value(a, "bond_volatility"))
    assert mix.volatility(alloc, a) < naive


def test_user_assumption_overrides_default_and_is_labelled():
    a = A.resolve({"inflation": 4.2})
    assert A.value(a, "inflation") == 4.2
    assert a["inflation"].confidence == "HIGH"
    assert "Set by you" in a["inflation"].basis


# ── scenarios ────────────────────────────────────────────────────────────

def test_every_scenario_runs_and_a_crash_hurts():
    p = profile_mod.load(BASE)
    a = A.resolve({})
    out = scenarios.run_all(p, a, retirement_age=58,
                            current_portfolio=600000, annual_savings=40000,
                            allocation=BASE["allocation"])
    rows = {r["key"]: r for r in out["scenarios"]}
    assert len(rows) == 8
    assert rows["bull"]["final_value"] > rows["normal"]["final_value"]
    assert rows["stagflation"]["final_value"] < rows["normal"]["final_value"]


def test_a_failing_scenario_is_priced_in_both_currencies_of_fix():
    """Every failure reports a spending cut and a capital number.

    A stress test that only says "this fails" leaves you with nothing to
    act on, so the engine always answers "and here is what it would take".
    """
    p = profile_mod.load(BASE)
    a = A.resolve({})
    out = scenarios.run_all(p, a, retirement_age=50,
                            current_portfolio=300000, annual_savings=20000,
                            allocation=BASE["allocation"])
    failures = [r for r in out["scenarios"] if not r["survives"]]
    assert failures
    for f in failures:
        assert "fix_cut" in f and "fix_capital" in f


# ── FI ladder ────────────────────────────────────────────────────────────

def test_ladder_is_ordered_and_lean_comes_first():
    l = fi.ladder(70000, 30000, 500000, 3.5)
    required = [r["required"] for r in l["rungs"]]
    assert required == sorted(required)
    assert l["rungs"][0]["level"] == "Lean"


def test_unreachable_target_returns_none_not_a_fantasy_number():
    assert fi.years_to_rung(50_000_000, 1000, 0, 0.0) is None


# ── storage ──────────────────────────────────────────────────────────────

def test_profile_and_snapshot_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_PROFILE", tmp_path / "profile.json")
    monkeypatch.setattr(store, "_SNAPSHOT", tmp_path / "snapshot.json")
    monkeypatch.setattr(store, "_HISTORY", tmp_path / "history.jsonl")

    store.save_profile(BASE)
    loaded = store.load_profile()
    assert loaded["retirement_age"] == 58
    assert loaded["updated_at"]

    snap = engine.build(loaded, run_monte_carlo=False, run_scenarios=False,
                        link_brokerage=False)
    store.save_snapshot(snap)
    assert store.load_snapshot()["target_portfolio"] == snap["target_portfolio"]
    assert len(store.history()) == 1


def test_snapshot_is_json_serialisable_with_numpy_inside():
    """numpy scalars have broken json.dumps() in this project before."""
    snap = build()
    json.dumps(store._json_safe(snap))


# ── the page ─────────────────────────────────────────────────────────────

def test_page_shows_intake_when_no_profile_exists(monkeypatch, tmp_path):
    from stockanalysis.webapp import retirement_view
    monkeypatch.setattr(store, "_PROFILE", tmp_path / "nope.json")
    body, _js = retirement_view.retirement_page({})
    assert "Set up your retirement profile" in body


def test_page_renders_a_full_snapshot(monkeypatch, tmp_path):
    from stockanalysis.webapp import retirement_view
    monkeypatch.setattr(store, "_PROFILE", tmp_path / "profile.json")
    monkeypatch.setattr(store, "_SNAPSHOT", tmp_path / "snapshot.json")
    monkeypatch.setattr(store, "_HISTORY", tmp_path / "history.jsonl")
    store.save_profile(BASE)
    body, _js = retirement_view.retirement_page({"refresh": ["1"]})
    for expected in ("Retirement snapshot", "Retirement timeline",
                     "Monte Carlo", "Stress tests", "Score breakdown",
                     "Assumptions", "not financial advice"):
        assert expected in body


def test_route_and_nav_are_wired():
    from stockanalysis.webapp import app, views
    assert "/retirement" in app.ROUTES
    assert any(n[0] == "retirement" for n in views.NAV)

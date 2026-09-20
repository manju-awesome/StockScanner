"""
retirement_view.py
==================
The Retirement page. Presentation only — every figure comes from
core.retirement and is read out of the snapshot the engine wrote.
Nothing here recomputes a number.

What the layout is arguing
--------------------------
The page opens with the SNAPSHOT, and the snapshot leads with the
survival probability rather than with net worth. Net worth is the number
people want to see and the one least connected to the question; a
probability of the plan lasting is the answer to the actual question and
belongs above the fold.

The retirement TIMELINE is placed before the portfolio detail for the
same reason. "You can retire at 51 on optimistic assumptions and 66 on
cautious ones" is the fact that changes behaviour. An asset allocation
table does not.

Every unscored, missing or assumed figure says so on the page rather
than in a footnote. This engine's whole claim is that it does not invent
numbers, and a layout that hides its gaps would be quietly breaking that
claim while looking more polished for doing it.
"""

from __future__ import annotations

from stockanalysis.core.retirement import store as ret_store

from .views import badge, card, empty, esc, fmt_money, progress_bar

DASH = "—"

_PRIORITY = {"HIGH": ("🔴", "bad"), "MEDIUM": ("🟠", "watch"),
             "LOW": ("🟢", "good")}
_LEVEL = {"HIGH": "bad", "MODERATE": "watch", "LOW": "good",
          "UNSCORED": "muted"}
_CONF = {"HIGH": ("🟢", "good"), "MEDIUM": ("🟡", "watch"),
         "LOW": ("🔴", "bad")}


def _m(v, nd=0):
    return DASH if v is None else fmt_money(v, nd)


def _p(v, nd=1):
    return DASH if v is None else f"{float(v):.{nd}f}%"


def _age(v):
    return DASH if v is None else f"{float(v):.0f}"


def _sub(text, colour="#898781"):
    return (f'<div style="font-size:10px;color:{colour};margin-top:3px;'
            f'line-height:1.45">{esc(text)}</div>')


def _stat(label, value, sub="", colour="#0b0b0b"):
    return (f'<div style="flex:1 1 150px;min-width:150px">'
            f'<div style="font-size:10px;color:#898781;text-transform:uppercase;'
            f'letter-spacing:.04em">{esc(label)}</div>'
            f'<div style="font-size:20px;font-weight:600;color:{colour};'
            f'margin-top:2px">{value}</div>'
            + (_sub(sub) if sub else "") + '</div>')


def _row_open(cols):
    return ('<div style="display:flex;flex-wrap:wrap;gap:18px">'
            + "".join(cols) + '</div>')


def _table(headers, rows, align=None):
    align = align or {}
    head = "".join(
        f'<th style="text-align:{align.get(i, "left")};padding:7px 10px;'
        f'font-size:10px;color:#898781;text-transform:uppercase;'
        f'letter-spacing:.04em;border-bottom:0.5px solid #e1e0d9;'
        f'white-space:nowrap">{h}</th>'
        for i, h in enumerate(headers))
    body = ""
    for r in rows:
        cells = "".join(
            f'<td style="text-align:{align.get(i, "left")};padding:8px 10px;'
            f'font-size:12px;border-bottom:0.5px solid #f1efea;'
            f'vertical-align:top">{c}</td>'
            for i, c in enumerate(r))
        body += f"<tr>{cells}</tr>"
    return (f'<div style="overflow-x:auto"><table style="width:100%;'
            f'border-collapse:collapse"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


# ─────────────────────────────────────────────────────────────────────────
# Panels
# ─────────────────────────────────────────────────────────────────────────

def _snapshot(s):
    score = (s.get("score") or {}).get("score")
    coverage = (s.get("score") or {}).get("coverage_pct")
    mc = s.get("monte_carlo") or {}
    prob = mc.get("success_pct")
    balance = s.get("balance") or {}
    risks = s.get("risks") or []
    actions = s.get("actions") or []

    top_risk = next((r for r in risks if r["level"] == "HIGH"), None)
    top_action = actions[0] if actions else None

    prob_colour = ("#1a7f4b" if (prob or 0) >= 80 else
                   "#a86414" if (prob or 0) >= 70 else "#b3261e")
    score_colour = ("#1a7f4b" if (score or 0) >= 75 else
                    "#a86414" if (score or 0) >= 55 else "#b3261e")

    cols = [
        _stat("Retirement score",
              f"{score:.0f}<span style='font-size:13px;color:#898781'>/100</span>"
              if score is not None else DASH,
              f"Scored on {coverage:.0f}% of the total weight."
              if coverage else "", score_colour),
        _stat("Survival probability", _p(prob, 0),
              f"{mc.get('band', '')} · {mc.get('paths', 0):,} simulated "
              f"sequences" if prob is not None else "Not run", prob_colour),
        _stat("Net worth", _m(balance.get("net_worth")),
              f"{_m(balance.get('investable'))} investable"),
        _stat("Retirement assets", _m(balance.get("retirement_assets")),
              "After capital committed to other goals"),
        _stat("Target portfolio", _m(s.get("target_portfolio")),
              "Planning number across withdrawal models"),
        _stat("FI progress", _p(s.get("funding_pct"), 0),
              f"{_m(s.get('target_portfolio'))} needed"),
    ]

    bars = ""
    if s.get("funding_pct") is not None:
        bars = ('<div style="margin-top:14px">'
                + progress_bar(min(s["funding_pct"], 100))
                + _sub(f"Retirement-funding assets against the planning "
                       f"target. Excludes the home and any capital "
                       f"earmarked for other goals.") + '</div>')

    lines = ""
    if top_risk:
        lines += (f'<div style="margin-top:12px;font-size:12px">'
                  f'<b>Biggest risk:</b> {esc(top_risk["risk"])} — '
                  f'{esc(top_risk["detail"])}</div>')
    if top_action:
        lines += (f'<div style="margin-top:6px;font-size:12px">'
                  f'<b>Biggest opportunity:</b> {esc(top_action["title"])}'
                  f'</div>')

    stamp = (f'<span style="font-size:10px;color:#898781">Computed '
             f'{esc((s.get("generated_at") or "")[:16].replace("T", " "))}'
             f'</span>')
    return card("Retirement snapshot", _row_open(cols) + bars + lines,
                icon="🎯", right=stamp)


def _missing_panel(s):
    missing = s.get("missing") or []
    if not missing:
        return ""
    items = "".join(f'<li style="margin-bottom:3px">{esc(m)}</li>'
                    for m in missing)
    body = (f'<div style="font-size:12px;color:#5a5852;margin-bottom:8px">'
            f'Conclusions that depend on these are omitted rather than '
            f'estimated. Add them to '
            f'<code>{esc(str(ret_store.profile_path()))}</code> and '
            f'refresh.</div>'
            f'<ul style="margin:0;padding-left:18px;font-size:12px;'
            f'color:#0b0b0b">{items}</ul>')
    return card(f"{len(missing)} inputs still missing", body, icon="📝")


def _timeline_panel(s):
    tl = s.get("timeline")
    if not tl:
        return ""
    rows = []
    for r in tl["rows"]:
        conf = _CONF.get(r.get("confidence", ""), ("", "muted"))
        rows.append([
            f'<b>{esc(r["label"])}</b>',
            (f'<span style="font-size:17px;font-weight:600">'
             f'{_age(r["age"])}</span>' if r["age"] else
             '<span style="color:#898781">not reachable</span>'),
            f'{conf[0]} {esc(r["basis"])}',
        ])
    note = _sub(tl["note"])
    extra = ""
    if tl.get("cost_of_certainty_years"):
        extra += (f'<div style="font-size:12px;margin-top:10px">'
                  f'<b>Cost of certainty:</b> '
                  f'{tl["cost_of_certainty_years"]} extra years of work '
                  f'between the optimistic date and the 90%-survival '
                  f'date.</div>')
    if tl.get("flexibility_saves_years"):
        extra += (f'<div style="font-size:12px;margin-top:4px">'
                  f'<b>Flexibility is worth '
                  f'{tl["flexibility_saves_years"]} years:</b> the same 80% '
                  f'survival arrives that much earlier if you will cut '
                  f'discretionary spending in a bad decade.</div>')
    return card("Retirement timeline",
                _table(["Basis", "Age", "What it assumes"], rows,
                       {1: "center"}) + extra + note, icon="📅")


def _models_panel(s):
    models = s.get("withdrawal_models")
    if not models:
        return ""
    rows = []
    a = models["A"]
    for r in a["rows"]:
        rows.append([f'A — fixed {r["rate"]:.1f}%', _m(r["required"]),
                     esc(r["note"])])
    for key in ("B", "C", "D"):
        m = models[key]
        rows.append([f'{esc(m["model"])}', _m(m["headline"]),
                     esc(m.get("note", ""))])

    spread = ""
    if models.get("spread_pct") is not None:
        spread = (f'<div style="font-size:12px;margin-top:10px">'
                  f'<b>Spread: {models["spread_pct"]:.0f}%</b> between the '
                  f'cheapest and dearest model '
                  f'({_m(models["low"])} – {_m(models["high"])}). '
                  f'{esc(models["interpretation"])}</div>')
    warn = ""
    if models.get("guardrail_warning"):
        warn = (f'<div style="font-size:12px;margin-top:6px;color:#b3261e">'
                f'{esc(models["guardrail_warning"])}</div>')
    planning = (f'<div style="font-size:12px;margin-top:8px">'
                f'<b>Planning number used on this page: '
                f'{_m(models["planning_number"])}</b> — the more demanding '
                f'of the dynamic model and a 3.5% fixed rate.</div>')
    return card("What the target should be — four models",
                _table(["Model", "Required portfolio", "What it assumes"],
                       rows, {1: "right"}) + planning + spread + warn,
                icon="📐")


def _fi_panel(s):
    ladder = s.get("fi_ladder")
    if not ladder:
        return ""
    rows = []
    for r in ladder["rungs"]:
        years = r.get("years_away")
        years_txt = (DASH if years is None else
                     "reached" if years <= 0 else f"{years:.1f} yrs")
        rows.append([
            f'<b>{esc(r["level"])}</b>' +
            (' ' + badge("reached", "good", "small") if r["reached"] else ""),
            _m(r["annual_spending"]),
            _m(r["required"]),
            _p(r["progress_pct"], 0),
            years_txt,
            _sub(r["note"]),
        ])
    return card("Financial independence ladder",
                _table(["Level", "Annual spending", "Required", "Progress",
                        "Years away", "What it means"], rows,
                       {1: "right", 2: "right", 3: "right", 4: "right"})
                + _sub(ladder["basis"]), icon="🪜")


def _balance_panel(s):
    b = s.get("balance") or {}
    if not b.get("total_assets"):
        return ""

    tax_rows = [[esc(k.replace("_", " ").title()), _m(v)]
                for k, v in (b.get("by_tax") or {}).items() if v]
    liq_rows = [[esc(k.replace("_", " ").title()), _m(v)]
                for k, v in (b.get("by_liquidity") or {}).items() if v]

    cols = [
        _stat("Total assets", _m(b.get("total_assets"))),
        _stat("Total debt", _m(b.get("total_debt"))),
        _stat("Net worth", _m(b.get("net_worth"))),
        _stat("Investable", _m(b.get("investable")),
              "Excludes the home you live in"),
        _stat("Committed elsewhere", _m(b.get("earmarked")),
              "Education, emergency, property"),
        _stat("Funding retirement", _m(b.get("retirement_assets"))),
    ]

    detail = (f'<div style="display:flex;gap:24px;flex-wrap:wrap;'
              f'margin-top:14px">'
              f'<div style="flex:1 1 220px"><div style="font-size:10px;'
              f'color:#898781;text-transform:uppercase;margin-bottom:4px">'
              f'By tax treatment</div>'
              + _table(["Bucket", "Value"], tax_rows, {1: "right"})
              + '</div><div style="flex:1 1 220px">'
              f'<div style="font-size:10px;color:#898781;'
              f'text-transform:uppercase;margin-bottom:4px">By liquidity'
              f'</div>'
              + _table(["Class", "Value"], liq_rows, {1: "right"})
              + '</div></div>')

    unknown = ""
    if b.get("unknown_count"):
        unknown = _sub(f'{b["unknown_count"]} asset(s) have no value '
                       f'recorded and are excluded from every total — '
                       f'missing, not zero.', "#b3261e")

    return card("Balance sheet", _row_open(cols) + detail + unknown,
                icon="🧾")


def _concentration_panel(s):
    b = s.get("balance") or {}
    conc = b.get("concentration") or []
    brokerage = b.get("brokerage") or {}
    if not conc:
        return ""
    rows = []
    for c in conc[:12]:
        rows.append([
            f'{c["flag"]} <b>{esc(c["ticker"])}</b>',
            _m(c["value"]),
            _p(c["weight"], 1),
            badge(c["band"], "bad" if c["band"] == "HIGH" else
                  "watch" if c["band"] == "MODERATE" else "good", "small"),
            esc(c.get("strategy") or ""),
        ])
    note = _sub(
        f'Weights are within the brokerage account only '
        f'({_m(brokerage.get("total"))}), as last synced '
        f'{esc((brokerage.get("as_of") or "?")[:16].replace("T", " "))}. '
        f'This panel classifies concentration; it does not tell you to '
        f'sell. A large position you chose and understand is a different '
        f'thing from one you drifted into, and this engine cannot tell '
        f'them apart.')
    return card("Position concentration",
                _table(["Ticker", "Value", "Weight", "Band", "Strategy"],
                       rows, {1: "right", 2: "right"}) + note, icon="🎚")


def _emergency_panel(s):
    ef = s.get("emergency_fund") or {}
    if ef.get("error") or not ef:
        return ""
    cols = [
        _stat("Held in cash", _m(ef.get("held_cash"))),
        _stat("Minimum (3 mo)", _m(ef.get("minimum"))),
        _stat("Recommended",
              f'{_m(ef.get("recommended"))}',
              f'{ef.get("months_recommended", 0):.0f} months of essentials'),
        _stat("Upper bound", _m(ef.get("maximum")),
              "Above this, cash is a drag"),
    ]
    reasons = "".join(f'<li style="margin-bottom:2px">{esc(r)}</li>'
                      for r in ef.get("reasons", []))
    verdict = badge(ef.get("verdict", ""), ef.get("status", "muted"))
    body = (_row_open(cols)
            + f'<div style="margin-top:12px">{verdict}</div>'
            + f'<ul style="margin:8px 0 0;padding-left:18px;font-size:11px;'
              f'color:#5a5852">{reasons}</ul>'
            + _sub("Sized on essential spending, not total — in a real "
                   "emergency the discretionary budget is the first thing "
                   "to go."))
    return card("Emergency fund", body, icon="🛟")


def _debt_panel(s):
    b = s.get("balance") or {}
    liabs = b.get("liabilities") or []
    if not liabs:
        return ""
    rows = []
    for l in liabs:
        d = l["disposition"]
        status = ("bad" if d["action"].startswith("PAY DOWN") else
                  "watch" if "REFINANCE" in d["action"] else
                  "muted" if d["action"] == "REVIEW" else "good")
        rows.append([
            f'<b>{esc(l["label"])}</b>',
            _m(l.get("balance")),
            _p(l.get("rate"), 2),
            _m(l.get("monthly_payment")),
            badge(d["action"], status, "small"),
            _sub(d["reason"]),
        ])
    dti = b.get("debt_to_income_pct")
    foot = _sub(f'Debt service is {dti:.1f}% of gross income. Dispositions '
                f'compare each debt\'s REAL rate against the portfolio\'s '
                f'expected REAL return, and require a margin — a certain '
                f'cost and an uncertain return are not equal at the same '
                f'number.' if dti is not None else "")
    return card("Debt", _table(
        ["Debt", "Balance", "Rate", "Monthly", "Read", "Why"], rows,
        {1: "right", 2: "right", 3: "right"}) + foot, icon="🏦")


def _montecarlo_panel(s):
    mc = s.get("monte_carlo") or {}
    if mc.get("error"):
        return card("Monte Carlo", empty(mc["error"]), icon="🎲")
    if not mc:
        return ""
    flex = s.get("monte_carlo_flexible") or {}
    t = mc.get("terminal") or {}

    cols = [
        _stat("Rigid plan", _p(mc.get("success_pct"), 0),
              "Spending never changes", "#b3261e"
              if (mc.get("success_pct") or 0) < 70 else "#0b0b0b"),
        _stat("With flexibility", _p(flex.get("success_pct"), 0),
              f'{s.get("flexibility_gain_pts", 0):+.0f} points from a '
              f'willingness to cut'),
        _stat("Median ending value", _m(t.get("p50")),
              "Real, today's dollars"),
        _stat("Bottom decile", _m(t.get("p10")),
              "1 path in 10 ends below this"),
        _stat("Worst path", _m(t.get("worst"))),
    ]
    body = (_row_open(cols)
            + _sub(f'{mc.get("paths", 0):,} paths · real return '
                   f'{mc.get("mu_pct")}%/yr, volatility '
                   f'{mc.get("sigma_pct")}%/yr, both derived from your '
                   f'allocation · horizon to age {mc.get("horizon_age")}')
            + _sub(mc.get("caveat", ""), "#b3261e"))
    return card("Monte Carlo", body, icon="🎲")


def _scenarios_panel(s):
    sc = s.get("scenarios") or {}
    rows_in = sc.get("scenarios") or []
    if not rows_in:
        return ""
    rows = []
    for r in rows_in:
        if r["survives"]:
            outcome = badge("survives", "good", "small")
            fix = _sub("—")
        else:
            outcome = badge(
                f'fails at {r["depleted_age"]:.0f}' if r["depleted_age"]
                else "fails", "bad", "small")
            cut = r.get("fix_cut") or {}
            cap = r.get("fix_capital")
            if cut.get("viable"):
                fix = _sub(f'Cut spending {cut["cut_pct"]:.1f}%, or add '
                           f'{_m(cap)} of capital today.')
            elif cut.get("cut_pct"):
                fix = _sub(f'A {cut["cut_pct"]:.1f}% cut would fix it but '
                           f'falls below essential spending. Needs '
                           f'{_m(cap)} more capital instead.', "#b3261e")
            else:
                fix = _sub(f'No spending cut fixes this. Needs {_m(cap)} '
                           f'more capital, a later date, or both.',
                           "#b3261e")
        rows.append([
            f'<b>{esc(r["title"])}</b>' + _sub(r["blurb"]),
            _m(r["portfolio_at_retirement"]),
            _p(r["withdrawal_rate"], 1),
            _m(r["final_value"]),
            outcome, fix,
        ])
    verdict = (f'<div style="font-size:12px;margin-top:10px">'
               f'{esc(sc.get("verdict", ""))}</div>')
    return card(
        f'Stress tests — {sc.get("survived", 0)} of '
        f'{len(rows_in)} histories survived',
        _table(["Scenario", "At retirement", "Withdrawal rate",
                "Ending value", "Outcome", "What would fix it"], rows,
               {1: "right", 2: "right", 3: "right"}) + verdict, icon="🌪")


def _risk_panel(s):
    risks = s.get("risks") or []
    if not risks:
        return ""
    rows = [[f'<b>{esc(r["risk"])}</b>',
             badge(r["level"], _LEVEL.get(r["level"], "muted"), "small"),
             esc(r["detail"])] for r in risks]
    return card("Risk register", _table(["Risk", "Level", "Detail"], rows),
                icon="⚠️")


def _score_panel(s):
    sc = s.get("score") or {}
    comps = sc.get("components") or []
    if not comps:
        return ""
    rows = []
    for c in comps:
        if c["scored"]:
            val = (f'<b>{c["score"]:.0f}</b>'
                   f'<span style="color:#898781">/100</span>')
            lost = f'−{c["points_lost"]:.1f}'
        else:
            val = '<span style="color:#898781">not scored</span>'
            lost = DASH
        rows.append([f'<b>{esc(c["label"])}</b>', f'{c["weight"]}%', val,
                     lost, _sub(c["why"])])
    foot = _sub(sc.get("note", ""))
    drags = sc.get("biggest_drags") or []
    drag_txt = ""
    if drags:
        drag_txt = (f'<div style="font-size:12px;margin-top:10px">'
                    f'<b>Biggest drags:</b> ' +
                    ", ".join(f'{esc(d["label"])} '
                              f'(−{d["points_lost"]:.1f} pts)'
                              for d in drags) + '</div>')
    return card(
        f'Score breakdown — {sc.get("score", DASH)}/100',
        _table(["Component", "Weight", "Score", "Points lost", "Why"], rows,
               {1: "right", 2: "right", 3: "right"}) + drag_txt + foot,
        icon="📊")


def _actions_panel(s):
    actions = s.get("actions") or []
    if not actions:
        return ""
    blocks = ""
    for group in ("HIGH", "MEDIUM", "LOW"):
        items = [a for a in actions if a["priority"] == group]
        if not items:
            continue
        icon, status = _PRIORITY[group]
        blocks += (f'<div style="margin-bottom:12px">'
                   f'<div style="font-size:11px;font-weight:600;'
                   f'margin-bottom:6px">{icon} {group} PRIORITY</div>')
        for a in items:
            blocks += (f'<div style="border-left:2px solid #e1e0d9;'
                       f'padding:2px 0 2px 10px;margin-bottom:8px">'
                       f'<div style="font-size:13px;font-weight:600">'
                       f'{esc(a["title"])}</div>'
                       f'<div style="font-size:12px;color:#5a5852;'
                       f'margin-top:2px;line-height:1.5">'
                       f'{esc(a["why"])}</div></div>')
        blocks += '</div>'
    return card("What to do next", blocks + _sub(
        "Items to consider and questions to take to a professional, each "
        "derived from a specific number above. Nothing here is investment, "
        "tax or legal advice."), icon="✅")


def _assumptions_panel(s):
    rows = []
    for a in s.get("assumptions") or []:
        icon, status = _CONF.get(a["confidence"], ("", "muted"))
        rows.append([
            f'<b>{esc(a["label"])}</b>',
            f'{a["value"]:g} {esc(a["unit"])}',
            f'{icon} {a["confidence"]}',
            _sub(a["basis"]),
        ])
    mix = s.get("mix") or {}
    w = mix.get("weights") or {}
    derived = (f'<div style="font-size:12px;margin-bottom:10px">'
               f'<b>Derived from your allocation</b> '
               f'({w.get("equity", 0):.0f}% equity / '
               f'{w.get("bond", 0):.0f}% bonds / {w.get("cash", 0):.0f}% '
               f'cash): expected real return '
               f'{mix.get("expected_real_return", 0):.2f}%/yr, volatility '
               f'{mix.get("volatility", 0):.1f}%/yr. Return is a '
               f'consequence of the allocation, not a free parameter.'
               f'</div>')
    return card("Assumptions", derived + _table(
        ["Assumption", "Value", "Confidence", "Basis"], rows,
        {1: "right", 2: "center"}) + _sub(
        "Everything on this page is in real (today's-dollar) terms. No tax "
        "rule is modelled anywhere in this engine."), icon="🔍")


def _intake_page():
    """What the page says before any profile exists.

    Shows the exact file to write and a worked skeleton, rather than a
    form. The profile is a document you will revise for years; a JSON
    file you own is a better home for it than fifty input boxes, and it
    means the whole plan is diffable and backed up with the repo.
    """
    path = ret_store.profile_path()
    skeleton = '''{
  "people": [
    {"name": "You",    "age": 38, "role": "self"},
    {"name": "Spouse", "age": 36, "role": "spouse"},
    {"name": "Child",  "age": 6,  "role": "child"}
  ],
  "retirement_age": 55,
  "current_country": "US",
  "retirement_country": "US",

  "income":   {"salary": 0, "spouse_salary": 0, "rsu": 0, "rental": 0},
  "savings":  {"401k": 0, "roth": 0, "brokerage": 0, "hsa": 0},

  "spending": {
    "essential": {"housing": 0, "food": 0, "utilities": 0,
                  "transportation": 0, "healthcare": 0, "insurance": 0,
                  "education": 0, "family_support": 0, "taxes": 0},
    "lifestyle": {"travel": 0, "entertainment": 0, "restaurants": 0,
                  "hobbies": 0, "shopping": 0}
  },
  "retirement_spending": {"essential": 0, "lifestyle": 0},

  "assets": [
    {"kind": "401k",       "value": 0},
    {"kind": "roth_ira",   "value": 0},
    {"kind": "hsa",        "value": 0},
    {"kind": "brokerage",  "value": null,
     "note": "null pulls the value from portfolio.csv"},
    {"kind": "cash",       "value": 0, "goal": "emergency"},
    {"kind": "primary_residence", "value": 0}
  ],

  "liabilities": [
    {"label": "Mortgage", "kind": "mortgage", "balance": 0,
     "rate": 6.25, "monthly_payment": 0}
  ],

  "one_offs": [
    {"label": "College", "amount": 0, "year": 2044,
     "goal": "education", "funded_from": "portfolio"}
  ],

  "allocation": {"equity": 0.80, "bond": 0.15, "cash": 0.05},
  "assumptions": {}
}'''
    body = (
        '<div style="font-size:13px;color:#5a5852;line-height:1.6">'
        'This engine will not produce a retirement number until it has '
        'the inputs to produce an honest one. Nothing here is estimated '
        'on your behalf.'
        '</div>'
        f'<div style="font-size:12px;margin-top:12px">Write your profile '
        f'to:<br><code style="font-size:11px">{esc(str(path))}</code></div>'
        f'<pre style="background:#faf9f6;border:0.5px solid #e1e0d9;'
        f'border-radius:8px;padding:12px;font-size:11px;overflow-x:auto;'
        f'margin-top:12px">{esc(skeleton)}</pre>'
        + _sub("Every field is optional. What you leave out is reported as "
               "missing and the conclusions that depend on it are omitted — "
               "which is the point.")
        + '<div style="margin-top:14px">'
          '<a href="/retirement?refresh=1" style="display:inline-block;'
          'background:#0b0b0b;color:white;padding:8px 14px;border-radius:8px;'
          'font-size:12px;text-decoration:none">Compute from profile.json'
          '</a></div>')
    return card("Set up your retirement profile", body, icon="🎯")


# ─────────────────────────────────────────────────────────────────────────

def retirement_page(query: dict | None = None) -> tuple[str, str]:
    query = query or {}
    refresh = bool((query.get("refresh") or [""])[0])

    raw = ret_store.load_profile()
    if not raw:
        return _intake_page(), ""

    snap = ret_store.load_snapshot()
    stale = (not snap
             or snap.get("profile_updated_at") != raw.get("updated_at"))
    if refresh or stale:
        from stockanalysis.core.retirement import engine
        snap = engine.build(raw)
        ret_store.save_snapshot(snap)

    body = (
        _snapshot(snap)
        + _missing_panel(snap)
        + _timeline_panel(snap)
        + _models_panel(snap)
        + _fi_panel(snap)
        + _montecarlo_panel(snap)
        + _scenarios_panel(snap)
        + _risk_panel(snap)
        + _balance_panel(snap)
        + _concentration_panel(snap)
        + _emergency_panel(snap)
        + _debt_panel(snap)
        + _score_panel(snap)
        + _actions_panel(snap)
        + _assumptions_panel(snap)
        + card("", '<div style="font-size:11px;color:#898781;line-height:1.6">'
               'This page is a modelling tool, not financial advice. It is '
               'not produced by a licensed advisor. Every output is a '
               'consequence of the assumptions listed above, and no tax '
               'rule — US, Indian or treaty — is modelled anywhere in it. '
               'Decisions about contributions, conversions, withdrawals or '
               'cross-border treatment belong with a CFP, CPA or CA.'
               '</div>', pad="12px 18px")
        + f'<div style="margin-top:4px"><a href="/retirement?refresh=1" '
          f'style="font-size:11px;color:#185FA5">Recompute</a></div>'
    )
    return body, ""

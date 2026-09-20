# Prompt: Cash-Secured Put candidates from the Long-Term engine

Paste the block below. It is written against the exact field names emitted by
`core/longterm/screen.py::flatten()`, so every rule is machine-checkable.

---

Run the `/longterm` engine over LONGTERM_TICKERS and build a cash-secured put
worksheet. Do not use `stock_scan_*_longterm.csv` — its `FCF_Positive` column
is empty, which forces `Investment_Pass=False` and `Category=Avoid` on
everything. Source eligibility from the engine directly.

**Step 0 — Regime.** Run `/market-regime` first. Report the regime and breadth.
If regime is DEFENSIVE, halve every position count in the final table and say so.
If a breadth or options-positioning input is missing, report the gap — never
estimate it.

**Step 1 — Eligibility (quality + undervalued).** Keep a ticker only if ALL hold.
Any field that is `None` fails the rule; list what was dropped and why.

*Quality — the business must be intact:*
- `lq_tier` in ("Elite", "High Quality")   — i.e. `lquality >= 80`
- `lq_coverage >= 70`                      — do not trust a tier built on half the inputs
- `investment_status` in ("CORE", "OWN")
- `action` is not in ("AVOID", "THESIS BROKEN", "RESEARCH")
- `lt_price >= 20` — sub-$20 names make the premium-per-contract not worth the
  assignment capital

*Discount — I want these genuinely cheap, not merely not-expensive:*
- `valuation_band == "UNDERVALUED"` (rank these first). Allow "FAIR" only if
  `upside >= 20`, and mark those rows as second-tier.
- `valuation_confidence` is not "low"
- `growth_gap` negative — the market is pricing in *less* growth than the
  company has delivered. This is the discount I am actually buying.
- `Dist_52W_High%` below -15 — off the high, not at it.

*Not a falling knife — the discount must be valuation, not a broken thesis:*
- `stage` is not "Stage 4"
- `trend_state` is not a broken/downtrend state
- `lt_rs_rank >= 40` — a name making new relative lows is repricing, not
  discounted

Selling a put is a bullish-to-neutral position you must be willing to have
assigned. Anything that fails the buy gate also fails the put-sell gate — do
not relax this to reach for premium.

**The central tension, and how to resolve it:** deep discount *and* fat premium
usually means the market is repricing something real. The three groups above
are what separates the two cases — the business (quality) and the trend
(stage/RS) must be intact while only the *valuation* is depressed. If a name
clears the discount rules but fails quality or trend, it is a value trap: list
it in the rejected table under "discount is thesis-driven, not valuation-driven"
and do not sell puts on it.

**Step 2 — Strike = the engine's own entry price.** Do not pick a strike by
delta first. For each survivor, take the priced pullback levels the engine
already computes and choose the strike as the nearest listed strike at or below
that price:

- If `action` is "BUY ON 50 MA" → use the 50 MA price (`lt_pct_vs_50ma` applied
  to `lt_price`).
- If `action` is "BUY ON 8/21 EMA" → use the 21 EMA price.
- If `action` is "BUY ON 200 MA" → use the 200 MA price.
- Otherwise → use `s1_price` (the buy-zone support), and prefer it when
  `s1_tested` is true and `s1_touches >= 2`.

Then sanity-check the chosen strike: it must be **below** `lt_price`, and the
resulting delta must land in **0.15–0.30**. If the engine's level implies a
delta below 0.15 the premium will be noise — mark the ticker "level too far,
no trade" rather than moving the strike up. State the implied delta.

**Step 3 — Expiry.** Pull the chain via the Robinhood MCP
(`get_option_chains` / `get_option_quotes`) for each survivor.

- Target **30–45 DTE** — the theta-decay sweet spot.
- Hard rule: the expiry must NOT span an earnings date. Check
  `lt_days_to_earnings`. If `lt_days_to_earnings <= 45`, either pick the last
  expiry that lands *before* earnings, or say "no clean expiry — skip".
  Never sell a put through earnings on an assignment-intent position.
- Prefer monthly expiries over weeklies for liquidity.

**Step 4 — Premium and volatility quality.** For each candidate strike/expiry,
report and rank on:

- **IV Rank** (current IV vs. that ticker's own trailing 1-year IV range), not
  absolute IV. Require `IV Rank >= 30` — below that you are not being paid to
  take assignment risk. Rank on IV Rank, not raw IV: raw IV just finds the
  jumpiest tickers in the universe, while IV Rank finds the ones rich *against
  their own history*, which is the premium I am actually harvesting.
- **Annualized return on capital** = `(premium / (strike * 100)) * (365 / DTE)`.
  This is the ranking column. Use the **bid**, not the mid — you are the seller.
- **Static return** = `premium / (strike * 100)` for the holding period.
- **Breakeven** = `strike - premium`, and breakeven as a % below `lt_price`.
- **Liquidity gate**: open interest >= 500, bid/ask spread <= 10% of mid.
  Fail either → drop, however good the premium.

If IV Rank is high but the name is otherwise clean, say *why* the IV is
elevated (earnings, sector event, index-level vol). An IV spike with no
identifiable cause on a quality name is the good case; an IV spike with a
pending catalyst is not.

**Step 5 — Output.** One table, sorted by annualized return on capital
descending:

| Ticker | LQuality | Val band | Upside% | Growth gap | Price | Strike | Level used | Delta | Expiry | DTE | Days to ER | Bid | Static% | Annualized% | IV Rank | Breakeven | BE % below | OI | Capital at risk |

Split the table into **Tier 1 (UNDERVALUED)** and **Tier 2 (FAIR, upside >= 20)**.
Do not let a Tier 2 row with a fat premium outrank a Tier 1 row in how you
present it — the discount is the thesis, the premium is the payment for waiting.

Below the table:
- **Capital required** per position = `strike * 100`, and the total across all
  rows. Flag if the total exceeds available cash.
- **Rejected list** — ticker plus the single rule that dropped it.
- **Data gaps** — any field that came back `None`, named explicitly.

Do not place any orders. This is a worksheet for me to review.

---

## Notes

- `put_candidate.py` in `core/` scores put *buying* (exhaustion fades). It is
  unrelated to this workflow — do not reuse its `Put_Score`.
- The engine's priced levels are the whole point: a CSP strike chosen from a
  delta table is a bet on not being assigned, while a strike chosen from the
  50 MA entry is a limit order you get paid to place.

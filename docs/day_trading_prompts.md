# Day Trading Prompt Library

Copy-paste prompts for pre-market prep, entry/exit decisions, risk-reward checks, and
position sizing. Fill in the `[BRACKETED]` placeholders before sending. They are written
to force specific numeric answers (levels, sizes, ratios) instead of vague commentary,
and they match the metrics this project already computes (RVOL, VWAP, ORB, ATR, gaps,
prev-day high/low, SPY/QQQ context).

> These prompts structure your own analysis and discipline. No prompt output is a
> guarantee — always validate levels against your live chart before placing an order.

---

## 1. Pre-market watchlist builder

```
Act as a day-trading analyst. Today is [DATE]. Here are my candidate tickers with
pre-market data: [TICKER: gap %, pre-market volume, RVOL, catalyst/news, prev close,
pre-market high/low — one line per ticker].

For each ticker, tell me:
1. Is the gap likely to CONTINUE or FADE? Base this on: gap size vs ATR, RVOL, whether
   there is a real catalyst, and whether price is holding above/below pre-market VWAP.
2. The single most important price level to watch at the open (prev-day high/low,
   pre-market high/low, or a round number).
3. A/B/C grade for intraday tradability.

Then rank the top 3 and say which ONE setup you'd focus on and why. Reject anything
with RVOL under 1.5 or average daily volume under 1M shares — tell me which ones you
rejected and why.
```

## 2. Entry evaluation (the "should I take this trade" prompt)

```
INTC: price 120.35, gap ?% (now 0.00%), VWAP 122.55 (below), RVOL 0.88, ADX 23.2, ATR(daily) 11.14, prev-day H/L 130.74/117.63, pre-mkt H/L 131.00/121.52 | plan: entry ? stop ? target ? [C, score 47]
I'm considering a [LONG/SHORT] day trade on [INTC]. Current data:
- Price: [X], VWAP: [X], 9EMA: [X], 20EMA: [X] (on the [1/5]-min chart)
- Opening range (9:30–9:45): high [X], low [X]
- Prev-day high [X], prev-day low [X], today's gap: [X]%
- RVOL: [X], ATR (daily): [X]
- Market context: SPY is [above/below] VWAP and [trending up/down/chopping], VIX [X]
- Setup I see: [ORB breakout / VWAP reclaim / gap-and-go / pullback to 9EMA / other]
INTC: price 120.35, gap ?% (now 0.00%), VWAP 122.55 (below), RVOL 0.88, ADX 23.2, ATR(daily) 11.14, prev-day H/L 130.74/117.63, pre-mkt H/L 131.00/121.52 | plan: entry ? stop ? target ? [C, score 47]

Answer in this exact format:
1. VERDICT: Take it / Skip it / Wait for [specific condition]
2. CONFLUENCES FOR (count them) and AGAINST (count them)
3. EXACT ENTRY TRIGGER: the specific price + condition (e.g. "break of 9:45 ORB high
   at 184.20 with volume expansion"), not "look for strength"
4. INVALIDATION: the price where this setup is objectively wrong

Be adversarial: if fewer than 3 confluences line up, or the market context contradicts
the trade direction, tell me to skip it.
```

## 3. Stop-loss and target placement (ATR-anchored)

```
I'm entering [LONG/SHORT] [TICKER] at [ENTRY PRICE].
- Daily ATR: [X], ATR on the 5-min chart: [X]
- Nearest structure: [swing low/high at X, VWAP at X, ORB low/high at X,
  prev-day high/low at X]

Give me:
1. STOP: placed beyond structure, not at it (structure level ± a buffer of roughly
   0.1–0.25× the 5-min ATR so a wick doesn't tag me out). State the exact price and
   the dollar risk per share.
2. TARGET 1 (take 1/2 or 1/3 off): the nearest realistic resistance/support or
   1× my risk, whichever comes first. Exact price.
3. TARGET 2 (runner): next major level or 2–3× risk. Exact price.
4. Sanity check: is my stop distance less than ~1.5× the 5-min ATR? If it's wider,
   say "stop too wide for a day trade — reduce size or skip" and explain.

If the nearest logical stop makes the reward-to-risk under 2:1 to Target 1... say so
plainly and tell me the entry price that WOULD make it 2:1, so I can decide to wait
for a better entry instead of forcing this one.
```

## 4. Risk-reward check (run BEFORE the order, every time)

```
Check this trade plan for [TICKER]:
- Direction: [LONG/SHORT]
- Entry: [X]  |  Stop: [X]  |  Target 1: [X]  |  Target 2: [X]

Compute and show your work:
1. Risk per share = |entry − stop|
2. Reward-to-risk at Target 1 and Target 2
3. Break-even win rate needed at each R:R (e.g. 2:1 needs >33% wins)
4. VERDICT: Pass (T1 is at least 2:1) or Fail. If Fail, give me the exact entry or
   stop adjustment that fixes it — or say "no valid trade here."

Do not soften the verdict. If the numbers don't work, the trade doesn't exist.
```

## 5. Position sizing (fixed-risk model)

```
Size this trade for me:
- Account size: [X]
- Max risk per trade: [1]% of account  (my hard rule — never exceed it)
- Entry: [X], Stop: [X]  →  risk per share = |entry − stop|
- Ticker: [TICKER], average 1-min volume: [X], spread: [X]

Give me:
1. SHARES = (account × risk%) ÷ risk per share — rounded DOWN to the nearest
   [1/5/10] shares. Show the math.
2. Total position cost, and whether it exceeds [25]% of my account or my buying
   power — if yes, cap the size and tell me my new effective risk %.
3. Liquidity check: is my size under ~1% of average 1-min volume so I can exit
   instantly? If not, cap it.
4. Final order: "BUY/SELL [N] shares at [entry], stop [X], risking $[X] = [X]% of
   account."

If I've already lost [2]% of the account today, remind me my daily loss limit is hit
and the answer is 0 shares.
```

## 6. In-trade management (exits after entry)

```
I'm in a [LONG/SHORT] on [TICKER]: [N] shares from [ENTRY], stop at [STOP].
Current price: [X]. Target 1 was [X], Target 2 was [X].
What's happening now: [e.g. "hit Target 1", "stalling at VWAP", "5-min candle closed
against me", "up 1.5R but volume drying up", "news just dropped"].

Tell me exactly one of:
- HOLD: stop stays at [X] because [reason]
- SCALE: sell [N] shares here, move stop to [exact price — breakeven or structure]
- EXIT ALL: because [invalidation reason]

Rules to enforce on me:
- After Target 1 fills, stop moves to breakeven — no exceptions.
- Never move a stop AWAY from price. If I ask you to, refuse.
- If the setup's original reason is gone (lost VWAP on a VWAP trade, back inside the
  opening range on an ORB trade), the trade is over even if the stop isn't hit.
- After [11:30] ET, if the trade is going nowhere (< 0.5R either way), tell me to
  flatten and stop watching it.
```

## 7. End-of-day trade review (journal prompt)

```
Review today's trades. For each: [TICKER, long/short, entry, stop, exit(s), size,
planned R:R, actual result in R, the setup name, and one sentence on why I entered].

For each trade answer:
1. Was the LOSS/WIN a good trade or a bad trade? (A planned 1R loss on a valid setup
   is a GOOD trade. A win from breaking my rules is a BAD trade — say so.)
2. Did I follow: entry trigger, planned stop, planned size, planned exits? List each
   as followed/violated.
3. One specific, mechanical fix — not "be more patient," but e.g. "don't enter ORB
   trades when RVOL < 1.5" or "set the stop order at the same time as the entry."

Then summarize: total R today, rule violations count, and the ONE pattern across
trades I should fix first.
```

## 8. Market-context gate (run once at 9:25 and again at 10:00)

```
Market context check, [DATE] [TIME] ET:
- SPY: [price vs VWAP, vs yesterday's close, trend on 5-min]
- QQQ: [same]
- VIX: [level, vs yesterday]
- Notable: [FOMC/CPI/earnings today? First trading day after holiday?]

Tell me:
1. Regime: TRENDING UP / TRENDING DOWN / CHOP
2. Which of my setups are allowed in this regime (breakouts need trend; fades and
   VWAP mean-reversion suit chop) and which are banned today
3. Size adjustment: full size / half size / sit out — with a one-line reason
   (e.g. "FOMC at 2pm — half size, flat by 1:30")
```

---

## How to use these together

Daily flow: **#8** (context gate) → **#1** (watchlist) → **#2** (entry check) →
**#3** (stop/target) → **#4** (R:R gate) → **#5** (size) → **#6** (manage) → **#7** (review).

Prompts #4 and #5 are the money-savers: they're pure arithmetic gates. If a trade
fails #4, prompts #5 and #6 never happen.

"""
broker_sync.py
==============
Merges broker-reported holdings into the user-owned CSVs (data/portfolio.csv
and data/options_positions.csv). Pure functions — no network, no broker
session, no disk I/O. The get-portfolio skill fetches the positions through
the Robinhood MCP tools and hands the payloads here; scripts/
sync_broker_positions.py does the reading and writing.

Why the split: the skill runs in a conversation, and conversations are not
tested. Every merge rule and every number lives here instead, where a test
pins it — the same reasoning as spy_prepare_order.py for the order path.

The merge is deliberately conservative
--------------------------------------
portfolio.csv is hand-maintained state: strategies, stops, targets, notes,
and hand-added columns like Target_Weight/Theme that drive the allocation
plan. A broker only knows ticker/quantity/cost. So the merge:

  * writes ONLY Shares and Avg_Cost from the broker, and never touches
    Strategy, Stop, Target, Notes, Entry_Date or any "_extra" column on a
    row that already exists;
  * never deletes a row. A holding that disappears from the broker is
    zeroed and marked closed, not removed — a watchlist row for something
    you just sold is useful, and a delete would take its notes with it;
  * leaves rows it doesn't own completely alone. Ownership is tracked in a
    Source column: a row this sync created says "robinhood", and a row you
    typed says nothing. Without that marker the sync couldn't tell "sold
    it" from "never held it, it's on my watchlist", and would zero out
    watchlist rows on every run.

Bookkeeping columns (Source, Last_Synced, Account) ride along in the
"_extra" dict rather than being added to POSITION_FIELDS, so the webapp's
Add/Edit Position form — which doesn't know about them — round-trips them
untouched instead of blanking them on the next edit.
"""

from __future__ import annotations

from datetime import date, datetime

SOURCE_BROKER = "robinhood"
SOURCE_COL = "Source"
SYNCED_COL = "Last_Synced"
ACCOUNT_COL = "Account"

# Strategy assigned to a holding the sync sees for the first time. It has to
# be one of portfolio.VALID_STRATEGIES, and it cannot be guessed correctly —
# the broker doesn't know whether you bought this to flip or to hold. It
# picks the setting whose alerts are about the thesis breaking (200MA lost,
# investment filters degraded) rather than about short-term timing, and every
# new row is listed under "needs_strategy" in the report so the choice is
# reviewed rather than silently inherited.
DEFAULT_SYNCED_STRATEGY = "longterm"

CLOSED_NOTE = "closed at broker"


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZING BROKER PAYLOADS
# ─────────────────────────────────────────────────────────────────────────────
# The MCP tools' exact key spellings aren't contractual, so every field is
# read through a list of accepted aliases. A renamed key costs a missing
# value in the report — not a KeyError halfway through writing the file.

def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _f(val) -> float | None:
    try:
        v = float(str(val).replace("$", "").replace(",", "").strip())
        return v if v == v else None
    except (TypeError, ValueError):
        return None


_ROW_KEYS = ("results", "positions", "instruments", "items",
             "equity_positions", "option_positions")


def _rows(payload) -> list[dict]:
    """Unwrap a broker payload into a list of row dicts.

    The MCP tools wrap everything in a `data` envelope — an equity fetch comes
    back as {"data": {"positions": [...]}} — so the search descends one level
    through `data` rather than only looking at the top. A version of this that
    only checked the top level returned [] for every real payload, which reads
    downstream as "the account holds nothing" and would mark every position
    closed. Hence the test that pins the exact nested shape.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for candidate in (payload, payload.get("data")):
        if not isinstance(candidate, dict):
            continue
        for key in _ROW_KEYS:
            if isinstance(candidate.get(key), list):
                return [r for r in candidate[key] if isinstance(r, dict)]
    # A single position handed over unwrapped
    if any(k in payload for k in ("symbol", "ticker", "chain_symbol")):
        return [payload]
    return []


def normalize_equities(*payloads) -> list[dict]:
    """Broker equity positions → [{Ticker, Shares, Avg_Cost}], zero-quantity
    rows dropped (the broker returns sold-out positions as quantity 0).

    Accepts several payloads so multiple accounts can be synced in one pass —
    syncing them one after another would make each run look like the previous
    account's positions had been sold. A ticker held in more than one account
    is *combined*, not overwritten: shares are summed and the cost basis is
    the share-weighted average, because portfolio.csv is one row per ticker
    and last-payload-wins would silently report only part of the position.
    """
    combined: dict[str, dict] = {}
    for payload in payloads:
        for r in _rows(payload):
            ticker = _first(r, "symbol", "ticker", "instrument_symbol", "chain_symbol")
            if not ticker:
                continue
            shares = _f(_first(r, "quantity", "shares", "qty", "quantity_available"))
            if not shares:
                continue
            ticker = str(ticker).strip().upper()
            cost = _f(_first(r, "average_buy_price", "average_cost", "avg_cost",
                             "average_price", "cost_basis_per_share"))
            prev = combined.get(ticker)
            if prev is None:
                combined[ticker] = {"Ticker": ticker, "Shares": shares,
                                    "Avg_Cost": cost}
                continue
            total = prev["Shares"] + shares
            if prev["Avg_Cost"] is not None and cost is not None and total:
                prev["Avg_Cost"] = round(
                    (prev["Avg_Cost"] * prev["Shares"] + cost * shares) / total, 4)
            elif prev["Avg_Cost"] is None:
                prev["Avg_Cost"] = cost
            prev["Shares"] = total
    return list(combined.values())


def index_instruments(payload) -> dict:
    """get_option_instruments → {instrument_id: row}. The positions endpoint
    doesn't carry strike or call/put — only an option_id — so the contract's
    terms have to be looked up and joined in. Without this every contract
    would default to a call with a blank strike, and showing a put as a call
    is a wrong number about real money, not a cosmetic gap."""
    return {str(r["id"]): r for r in _rows(payload) if r.get("id")}


# "type" is overloaded across Robinhood's endpoints: on a *position* it means
# long/short, on an *instrument* it means call/put. Reading it blindly makes a
# long position into a call-type contract. Each is only accepted from `type`
# when the value actually belongs to that vocabulary.
_OPTION_TYPES = ("call", "put")
_SIDES = ("long", "short")


def _pick(r: dict, keys: tuple, allowed: tuple) -> str | None:
    for k in keys:
        val = str(r.get(k) or "").strip().lower()
        if val in allowed:
            return val
    return None


def normalize_options(*payloads, instruments: dict | None = None) -> list[dict]:
    """Broker option positions → the options_positions.csv field names,
    zero-quantity rows dropped. `instruments` is index_instruments() output,
    used to fill in strike/type the positions payload doesn't carry.

    Takes several payloads for the same multi-account reason as
    normalize_equities(). Contracts are keyed by instrument id, so the same
    contract held in two accounts is summed rather than duplicated.
    """
    instruments = instruments or {}
    combined: dict[str, dict] = {}
    for payload in payloads:
        for r in _rows(payload):
            option_id = str(_first(r, "option_id", "instrument_id", "id", default="")).strip()
            inst = instruments.get(option_id, {})
            underlying = _first(r, "chain_symbol", "underlying_symbol", "symbol",
                                "ticker") or inst.get("chain_symbol")
            if not underlying:
                continue
            contracts = _f(_first(r, "quantity", "contracts", "qty"))
            if not contracts:
                continue

            otype = (_pick(inst, ("option_type", "contract_type", "type"), _OPTION_TYPES)
                     or _pick(r, ("option_type", "contract_type", "type"), _OPTION_TYPES)
                     or "call")
            # Signed quantity is unambiguous, so it wins over any label.
            side = ("short" if contracts < 0 else
                    _pick(r, ("side", "position_type", "direction", "type"), _SIDES)
                    or "long")
            multiplier = _f(_first(r, "trade_value_multiplier", "multiplier")) or 100.0

            row = {
                "Underlying":    str(underlying).strip().upper(),
                "Option_ID":     option_id,
                "Option_Symbol": str(_first(r, "option_symbol", "symbol_display",
                                            "contract_symbol", default="")).strip(),
                "Type":          otype,
                "Strike":        _f(_first(r, "strike_price", "strike")
                                    or inst.get("strike_price")),
                "Expiration":    _parse_date(_first(r, "expiration_date", "expiration",
                                                    "expires_at")
                                             or inst.get("expiration_date")),
                "Contracts":     abs(contracts),
                "Side":          side,
                "Avg_Premium":   _premium(r, multiplier),
                "Opened_Date":   _parse_date(_first(r, "opened_at", "created_at")),
            }
            key = option_id or str(_option_key(row))
            prev = combined.get(key)
            if prev is None:
                combined[key] = row
            else:
                total = prev["Contracts"] + row["Contracts"]
                if prev["Avg_Premium"] is not None and row["Avg_Premium"] is not None and total:
                    prev["Avg_Premium"] = round(
                        (prev["Avg_Premium"] * prev["Contracts"]
                         + row["Avg_Premium"] * row["Contracts"]) / total, 4)
                prev["Contracts"] = total
    return list(combined.values())


def _premium(r: dict, multiplier: float = 100.0) -> float | None:
    """Per-share premium. Robinhood reports option cost per *contract*
    (`average_price` of 340.00 means $3.40/share on a 100-multiplier
    contract), so it's divided back down to keep one unit throughout —
    storing it raw would overstate every options cost basis 100×."""
    per_share = _f(_first(r, "average_premium", "avg_premium", "average_price_per_share"))
    if per_share is not None:
        return per_share
    per_contract = _f(_first(r, "average_open_price", "average_price", "average_cost"))
    if per_contract is not None and multiplier:
        return round(per_contract / multiplier, 4)
    return None


def _parse_date(val) -> date | None:
    if isinstance(val, date):
        return val
    if not val:
        return None
    text = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MERGING
# ─────────────────────────────────────────────────────────────────────────────

def _stamp(extra: dict | None, account: str, synced_at: str) -> dict:
    out = dict(extra or {})
    out[SOURCE_COL] = SOURCE_BROKER
    out[SYNCED_COL] = synced_at
    if account:
        out[ACCOUNT_COL] = account
    return out


def _owned_by_broker(pos: dict) -> bool:
    return (pos.get("_extra") or {}).get(SOURCE_COL) == SOURCE_BROKER


def merge_equities(existing: list[dict], broker: list[dict], *,
                   account: str = "", synced_at: str | None = None,
                   default_strategy: str = DEFAULT_SYNCED_STRATEGY
                   ) -> tuple[list[dict], dict]:
    """
    Merge normalized broker holdings into loaded portfolio.csv positions.

    Returns (positions, report). `positions` is ready for
    portfolio.save_positions(). The report names every row that changed, so
    the skill can show the user what the sync did rather than just "done".
    """
    synced_at = synced_at or datetime.now().isoformat(timespec="seconds")
    by_ticker = {p["Ticker"]: p for p in existing}
    report = {"added": [], "updated": [], "unchanged": [], "closed": [],
              "needs_strategy": [], "manual_untouched": 0}

    for hold in broker:
        ticker = hold["Ticker"]
        current = by_ticker.get(ticker)
        if current is None:
            row = {
                "Ticker":     ticker,
                "Shares":     hold["Shares"],
                "Avg_Cost":   hold["Avg_Cost"],
                "Entry_Date": None,
                "Strategy":   default_strategy,
                "Stop":       None,
                "Target":     None,
                "Notes":      "",
                "_extra":     _stamp(None, account, synced_at),
            }
            existing.append(row)
            by_ticker[ticker] = row
            report["added"].append({"ticker": ticker, "shares": hold["Shares"],
                                    "avg_cost": hold["Avg_Cost"]})
            report["needs_strategy"].append(ticker)
            continue

        was = (current.get("Shares") or 0.0, current.get("Avg_Cost"))
        now = (hold["Shares"], hold["Avg_Cost"] if hold["Avg_Cost"] is not None
               else current.get("Avg_Cost"))
        # Only these two fields are broker-authoritative. Strategy, Stop,
        # Target, Notes, Entry_Date and every _extra column stay as the user
        # left them.
        current["Shares"] = now[0]
        current["Avg_Cost"] = now[1]
        current["_extra"] = _stamp(current.get("_extra"), account, synced_at)
        # A watchlist row you've now actually bought. Strategy stays "watch"
        # because the user set it and the broker doesn't get a vote — but
        # "watch" gates off the longterm/swing alerts, so a real position
        # would sit there with the thinnest alert set until someone noticed.
        # Flag it instead of silently changing it.
        if now[0] and current.get("Strategy") == "watch":
            report["needs_strategy"].append(ticker)
        if was != now:
            report["updated"].append({"ticker": ticker,
                                      "shares": [was[0], now[0]],
                                      "avg_cost": [was[1], now[1]]})
        else:
            report["unchanged"].append(ticker)

    # Rows this sync created that the broker no longer reports → sold. Zero
    # the position but keep the row (and its notes/theme) as a watchlist
    # entry. Rows the user typed are never touched by this branch.
    broker_tickers = {h["Ticker"] for h in broker}
    for pos in existing:
        if pos["Ticker"] in broker_tickers:
            continue
        if not _owned_by_broker(pos):
            report["manual_untouched"] += 1
            continue
        if (pos.get("Shares") or 0.0) == 0.0:
            continue
        report["closed"].append({"ticker": pos["Ticker"],
                                 "was_shares": pos.get("Shares")})
        pos["Shares"] = 0.0
        pos["Strategy"] = "watch"
        if CLOSED_NOTE not in (pos.get("Notes") or ""):
            note = (pos.get("Notes") or "").strip()
            pos["Notes"] = f"{note} · {CLOSED_NOTE} {date.today().isoformat()}".strip(" ·")
        pos["_extra"] = _stamp(pos.get("_extra"), account, synced_at)

    return existing, report


def _contract_label(opt: dict) -> str:
    """Readable contract for the report, e.g. "GOOGL 400C 2026-08-28".
    Robinhood's positions endpoint carries no contract symbol, so reports
    keyed on one would read as raw tuples or blanks."""
    strike = opt.get("Strike")
    exp = opt.get("Expiration")
    parts = [str(opt.get("Underlying") or "?")]
    if strike is not None:
        parts.append(f'{strike:g}{"C" if opt.get("Type") == "call" else "P"}')
    if isinstance(exp, date):
        parts.append(exp.isoformat())
    return " ".join(parts)


def _option_key(opt: dict) -> tuple:
    """Identity of a contract: the broker's instrument id, else its symbol,
    else the tuple that uniquely defines a contract. Robinhood's positions
    endpoint returns an id but no symbol, so without the id-first rule every
    sync would look like a brand-new position and re-add it."""
    option_id = (opt.get("Option_ID") or "").strip()
    if option_id:
        return ("id", option_id)
    symbol = (opt.get("Option_Symbol") or "").strip().upper()
    if symbol:
        return ("symbol", symbol)
    exp = opt.get("Expiration")
    return ("contract", opt.get("Underlying"), opt.get("Type"),
            opt.get("Strike"), exp.isoformat() if isinstance(exp, date) else "",
            opt.get("Side"))


def merge_options(existing: list[dict], broker: list[dict], *,
                  premiums: dict | None = None, account: str = "",
                  synced_at: str | None = None) -> tuple[list[dict], dict]:
    """
    Merge normalized broker option positions into loaded options_positions.csv
    rows. `premiums` maps Option_ID (or Option_Symbol) → current per-share
    premium from get_option_quotes; anything not quoted keeps its previous
    snapshot.

    Closed contracts are dropped, not zeroed — unlike a stock, an expired or
    sold contract has no watchlist meaning to preserve, and keeping it would
    leave a permanently-expired row alerting forever. They're named in the
    report so the run still tells you what left.
    """
    synced_at = synced_at or datetime.now().isoformat(timespec="seconds")
    premiums = {str(k).strip().upper(): v for k, v in (premiums or {}).items()}
    by_key = {_option_key(o): o for o in existing}
    broker_keys = set()
    report = {"added": [], "updated": [], "unchanged": [], "closed": [],
              "unquoted": [], "manual_untouched": 0}
    merged: list[dict] = []

    for hold in broker:
        key = _option_key(hold)
        broker_keys.add(key)
        current = by_key.get(key)
        label = _contract_label(hold)
        # Quotes are keyed by instrument id on Robinhood; symbol is the
        # fallback for brokers that give one.
        fresh = _first(premiums,
                       (hold.get("Option_ID") or "").strip().upper(),
                       (hold.get("Option_Symbol") or "").strip().upper())
        quote = fresh
        if quote is None and current is not None:
            quote = current.get("Current_Premium")
        elif quote is None:
            report["unquoted"].append(label)

        row = {
            **hold,
            "Current_Premium": quote,
            "Quote_At":        synced_at if fresh is not None
                               else (current or {}).get("Quote_At", ""),
            # User-owned fields survive a re-sync.
            "Strategy":        (current or {}).get("Strategy", ""),
            "Notes":           (current or {}).get("Notes", ""),
            "_extra":          _stamp((current or {}).get("_extra"), account, synced_at),
        }
        if current is None:
            report["added"].append({"contract": label,
                                    "contracts": hold.get("Contracts")})
        elif (current.get("Contracts"), current.get("Avg_Premium")) != \
             (hold.get("Contracts"), hold.get("Avg_Premium")):
            report["updated"].append({
                "contract": label,
                "contracts": [current.get("Contracts"), hold.get("Contracts")]})
        else:
            report["unchanged"].append(label)
        merged.append(row)

    for old in existing:
        key = _option_key(old)
        if key in broker_keys:
            continue
        if not _owned_by_broker(old):
            # Hand-entered contract the broker doesn't report — keep it.
            report["manual_untouched"] += 1
            merged.append(old)
            continue
        report["closed"].append({"contract": old.get("Option_Symbol") or str(key),
                                 "was_contracts": old.get("Contracts")})

    return merged, report

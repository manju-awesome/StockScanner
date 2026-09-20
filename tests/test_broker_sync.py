"""
Tests for core/broker_sync.py — the merge rules that decide what a broker is
allowed to overwrite in the user's hand-maintained portfolio.csv.

The destructive cases are the point of this file: a sync that eats a
watchlist row's Theme, resets a stop, or deletes a position is worse than a
sync that doesn't run.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import broker_sync  # noqa: E402
from stockanalysis.reporting import options_positions, portfolio  # noqa: E402


def manual_row(ticker, **over):
    row = {"Ticker": ticker, "Shares": 0.0, "Avg_Cost": None, "Entry_Date": None,
           "Strategy": "watch", "Stop": None, "Target": None, "Notes": ""}
    row.update(over)
    return row


def synced_row(ticker, **over):
    row = manual_row(ticker, **over)
    row.setdefault("_extra", {})
    row["_extra"] = {**row["_extra"], "Source": "robinhood"}
    return row


class TestRealRobinhoodShapes(unittest.TestCase):
    """Pinned against actual MCP responses. Every assertion here failed at
    least once against a payload shape that had been guessed."""

    def test_unwraps_the_nested_data_envelope(self):
        """The tools return {"data": {"positions": [...]}}. A version that
        only looked at the top level returned [] for every real payload —
        which reads downstream as "the account holds nothing" and marks every
        position closed."""
        out = broker_sync.normalize_equities(
            {"data": {"positions": [
                {"symbol": "OKLO", "quantity": "100.000000",
                 "average_buy_price": "76.760000", "type": "long"}]}})
        self.assertEqual(out, [{"Ticker": "OKLO", "Shares": 100.0,
                                "Avg_Cost": 76.76}])

    def test_position_type_long_is_not_read_as_a_call(self):
        """"type" means long/short on a position and call/put on an
        instrument. Reading it blindly turns a side into a contract type."""
        out = broker_sync.normalize_options({"data": {"positions": [{
            "option_id": "abc", "chain_symbol": "GOOGL", "type": "long",
            "quantity": "1.0000", "average_price": "340.0000",
            "expiration_date": "2026-08-28", "trade_value_multiplier": "100.0000"}]}})
        self.assertEqual(out[0]["Side"], "long")
        self.assertEqual(out[0]["Type"], "call", "default, not derived from 'long'")

    def test_instrument_lookup_supplies_strike_and_put_call(self):
        """The positions endpoint carries no strike or call/put — only an
        option_id. Showing a put as a call is a wrong number, not a gap."""
        instruments = broker_sync.index_instruments({"data": {"instruments": [
            {"id": "abc", "chain_symbol": "GOOGL", "strike_price": "400.0000",
             "type": "put", "expiration_date": "2026-08-28"}]}})
        out = broker_sync.normalize_options({"data": {"positions": [{
            "option_id": "abc", "chain_symbol": "GOOGL", "type": "long",
            "quantity": "1.0000", "average_price": "340.0000"}]}},
            instruments=instruments)
        self.assertEqual(out[0]["Strike"], 400.0)
        self.assertEqual(out[0]["Type"], "put")
        self.assertEqual(out[0]["Side"], "long")
        self.assertEqual(out[0]["Expiration"], date(2026, 8, 28))

    def test_average_price_is_per_contract(self):
        """average_price 340.00 on a 100-multiplier contract is $3.40/share.
        Storing it raw overstates the cost basis 100×."""
        out = broker_sync.normalize_options({"data": {"positions": [{
            "option_id": "abc", "chain_symbol": "GOOGL", "quantity": "1",
            "average_price": "340.0000", "trade_value_multiplier": "100.0000"}]}})
        self.assertEqual(out[0]["Avg_Premium"], 3.40)

    def test_same_ticker_in_two_accounts_is_combined_not_overwritten(self):
        """IREN held in two accounts is one row in portfolio.csv. Last-wins
        would report 100 shares when 100.5 are held, at the wrong basis."""
        acct_a = {"data": {"positions": [
            {"symbol": "IREN", "quantity": "0.500000", "average_buy_price": "59.820000"}]}}
        acct_b = {"data": {"positions": [
            {"symbol": "IREN", "quantity": "100.000000", "average_buy_price": "51.200000"}]}}
        out = broker_sync.normalize_equities(acct_a, acct_b)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["Shares"], 100.5)
        # share-weighted: (0.5×59.82 + 100×51.20) / 100.5
        self.assertAlmostEqual(out[0]["Avg_Cost"], 51.2429, places=3)

    def test_quotes_are_keyed_by_instrument_id(self):
        """Robinhood returns no contract symbol on positions, so a
        symbol-only premium lookup never matches and every contract silently
        shows no P&L."""
        broker = broker_sync.normalize_options({"data": {"positions": [{
            "option_id": "abc", "chain_symbol": "GOOGL", "quantity": "1",
            "average_price": "340.0000"}]}})
        merged, report = broker_sync.merge_options(
            [], broker, premiums={"abc": 0.34}, synced_at="2026-07-25T22:00:00")
        self.assertEqual(merged[0]["Current_Premium"], 0.34)
        self.assertEqual(merged[0]["Quote_At"], "2026-07-25T22:00:00")
        self.assertEqual(report["unquoted"], [])

    def test_resync_matches_on_option_id_and_does_not_duplicate(self):
        broker = broker_sync.normalize_options({"data": {"positions": [{
            "option_id": "abc", "chain_symbol": "GOOGL", "quantity": "1",
            "average_price": "340.0000"}]}})
        first, _ = broker_sync.merge_options([], broker, premiums={"abc": 0.34})
        second, report = broker_sync.merge_options(first, broker, premiums={"abc": 0.30})
        self.assertEqual(len(second), 1)
        self.assertEqual(report["added"], [])
        self.assertEqual(second[0]["Current_Premium"], 0.30)

    def test_report_labels_are_readable(self):
        broker = broker_sync.normalize_options(
            {"data": {"positions": [{"option_id": "abc", "chain_symbol": "GOOGL",
                                     "quantity": "1", "average_price": "340.0000",
                                     "expiration_date": "2026-08-28"}]}},
            instruments=broker_sync.index_instruments({"data": {"instruments": [
                {"id": "abc", "strike_price": "400.0000", "type": "call"}]}}))
        _, report = broker_sync.merge_options([], broker)
        self.assertEqual(report["added"][0]["contract"], "GOOGL 400C 2026-08-28")


class TestNormalizeEquities(unittest.TestCase):
    def test_reads_robinhood_key_spelling(self):
        out = broker_sync.normalize_equities({"results": [
            {"symbol": "nvda", "quantity": "25.0", "average_buy_price": "145.50"}]})
        self.assertEqual(out, [{"Ticker": "NVDA", "Shares": 25.0, "Avg_Cost": 145.5}])

    def test_accepts_bare_list_and_alias_keys(self):
        out = broker_sync.normalize_equities(
            [{"ticker": "AMD", "shares": 10, "average_cost": 100}])
        self.assertEqual(out[0]["Ticker"], "AMD")
        self.assertEqual(out[0]["Avg_Cost"], 100.0)

    def test_drops_zero_quantity_rows(self):
        """The broker returns sold-out positions as quantity 0 — treating one
        as a holding would resurrect a position the user already exited."""
        out = broker_sync.normalize_equities(
            [{"symbol": "OLD", "quantity": "0", "average_buy_price": "10"}])
        self.assertEqual(out, [])

    def test_unknown_shape_is_empty_not_an_exception(self):
        self.assertEqual(broker_sync.normalize_equities({"error": "nope"}), [])
        self.assertEqual(broker_sync.normalize_equities(None), [])


class TestMergeEquities(unittest.TestCase):
    def test_new_holding_is_added_and_flagged_for_strategy(self):
        positions, report = broker_sync.merge_equities(
            [], [{"Ticker": "NVDA", "Shares": 25.0, "Avg_Cost": 145.5}],
            account="1234", synced_at="2026-07-25T10:00:00")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["Shares"], 25.0)
        self.assertEqual(positions[0]["Strategy"], broker_sync.DEFAULT_SYNCED_STRATEGY)
        self.assertEqual(positions[0]["_extra"]["Source"], "robinhood")
        self.assertEqual(positions[0]["_extra"]["Account"], "1234")
        self.assertEqual(report["needs_strategy"], ["NVDA"])

    def test_existing_row_keeps_strategy_stop_target_and_notes(self):
        """The broker knows quantity and cost. It does not know the user's
        plan, and must not overwrite it."""
        existing = [synced_row("NVDA", Shares=10.0, Avg_Cost=100.0,
                               Strategy="swing", Stop=95.0, Target=160.0,
                               Notes="pullback add", Entry_Date=date(2026, 5, 1))]
        positions, report = broker_sync.merge_equities(
            existing, [{"Ticker": "NVDA", "Shares": 25.0, "Avg_Cost": 145.5}])
        p = positions[0]
        self.assertEqual((p["Shares"], p["Avg_Cost"]), (25.0, 145.5))
        self.assertEqual(p["Strategy"], "swing")
        self.assertEqual(p["Stop"], 95.0)
        self.assertEqual(p["Target"], 160.0)
        self.assertEqual(p["Notes"], "pullback add")
        self.assertEqual(p["Entry_Date"], date(2026, 5, 1))
        self.assertEqual(report["updated"][0]["ticker"], "NVDA")

    def test_watchlist_row_that_became_a_holding_is_flagged(self):
        """Strategy "watch" gates off the longterm/swing alerts. A row that
        now has shares must not keep that alert set unnoticed — the sync
        won't overwrite the user's setting, so it has to surface it."""
        existing = [synced_row("META", Shares=0.0, Strategy="watch")]
        _, report = broker_sync.merge_equities(
            existing, [{"Ticker": "META", "Shares": 12.0, "Avg_Cost": 505.25}])
        self.assertIn("META", report["needs_strategy"])
        self.assertEqual(existing[0]["Strategy"], "watch", "must not be overwritten")

    def test_watchlist_row_with_no_shares_is_not_flagged(self):
        existing = [manual_row("NOW", Strategy="watch")]
        _, report = broker_sync.merge_equities(existing, [])
        self.assertEqual(report["needs_strategy"], [])

    def test_hand_added_columns_survive(self):
        """Target_Weight/Theme drive the allocation plan and exist only in
        the CSV — a sync that dropped them would quietly break it."""
        existing = [synced_row("META", Shares=5.0,
                               _extra={"Source": "robinhood", "Target_Weight": "2.17%",
                                       "Theme": "AI Platform"})]
        positions, _ = broker_sync.merge_equities(
            existing, [{"Ticker": "META", "Shares": 8.0, "Avg_Cost": 500.0}])
        self.assertEqual(positions[0]["_extra"]["Target_Weight"], "2.17%")
        self.assertEqual(positions[0]["_extra"]["Theme"], "AI Platform")

    def test_manual_watchlist_row_is_never_zeroed(self):
        """A watchlist row the user typed isn't a sold position — the sync
        has no business touching it."""
        existing = [manual_row("NOW", Notes="waiting for base",
                               _extra={"Theme": "Enterprise AI Software"})]
        positions, report = broker_sync.merge_equities(existing, [])
        self.assertEqual(positions[0]["Notes"], "waiting for base")
        self.assertEqual(positions[0]["Strategy"], "watch")
        self.assertEqual(report["closed"], [])
        self.assertEqual(report["manual_untouched"], 1)

    def test_manual_held_position_is_not_zeroed_by_an_empty_payload(self):
        """A position typed by hand and not held at this broker must survive
        a sync of a different account."""
        existing = [manual_row("OKLO", Shares=100.0, Avg_Cost=76.0,
                               Strategy="longterm")]
        positions, report = broker_sync.merge_equities(existing, [])
        self.assertEqual(positions[0]["Shares"], 100.0)
        self.assertEqual(report["closed"], [])

    def test_broker_owned_position_that_vanished_is_closed_not_deleted(self):
        existing = [synced_row("TSLA", Shares=10.0, Avg_Cost=200.0,
                               Strategy="swing", Notes="momentum")]
        positions, report = broker_sync.merge_equities(existing, [])
        self.assertEqual(len(positions), 1, "row must be kept, not deleted")
        self.assertEqual(positions[0]["Shares"], 0.0)
        self.assertEqual(positions[0]["Strategy"], "watch")
        self.assertIn("momentum", positions[0]["Notes"])
        self.assertIn(broker_sync.CLOSED_NOTE, positions[0]["Notes"])
        self.assertEqual(report["closed"][0]["ticker"], "TSLA")

    def test_closed_note_is_not_appended_twice(self):
        existing = [synced_row("TSLA", Shares=10.0, Notes="momentum")]
        positions, _ = broker_sync.merge_equities(existing, [])
        positions[0]["Shares"] = 5.0            # pretend it came back and went again
        positions, _ = broker_sync.merge_equities(positions, [])
        self.assertEqual(positions[0]["Notes"].count(broker_sync.CLOSED_NOTE), 1)

    def test_round_trips_through_the_real_csv_writer(self):
        """merge output must be exactly what save_positions() accepts —
        the two were written separately and the bookkeeping columns ride in
        _extra specifically so this works."""
        import tempfile
        positions, _ = broker_sync.merge_equities(
            [manual_row("NOW", _extra={"Theme": "Enterprise AI Software"})],
            [{"Ticker": "NVDA", "Shares": 25.0, "Avg_Cost": 145.5}],
            account="1234")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            portfolio.save_positions(positions, path)
            reloaded = portfolio.load_positions(path)
        self.assertEqual({p["Ticker"] for p in reloaded}, {"NOW", "NVDA"})
        nvda = next(p for p in reloaded if p["Ticker"] == "NVDA")
        self.assertEqual(nvda["Shares"], 25.0)
        self.assertEqual(nvda["_extra"]["Source"], "robinhood")
        now = next(p for p in reloaded if p["Ticker"] == "NOW")
        self.assertEqual(now["_extra"]["Theme"], "Enterprise AI Software")

    def test_webapp_edit_preserves_sync_bookkeeping(self):
        """The Add/Edit Position form doesn't know about Source/Last_Synced;
        editing a synced row must not blank them."""
        import tempfile
        positions, _ = broker_sync.merge_equities(
            [], [{"Ticker": "NVDA", "Shares": 25.0, "Avg_Cost": 145.5}],
            account="1234")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            portfolio.save_positions(positions, path)
            portfolio.upsert_position(
                {"Ticker": "NVDA", "Shares": "25", "Avg_Cost": "145.50",
                 "Strategy": "swing", "Stop": "130"}, path=path)
            reloaded = portfolio.load_positions(path)
        self.assertEqual(reloaded[0]["Strategy"], "swing")
        self.assertEqual(reloaded[0]["_extra"]["Source"], "robinhood")


class TestNormalizeOptions(unittest.TestCase):
    def test_reads_robinhood_option_shape(self):
        out = broker_sync.normalize_options({"results": [{
            "chain_symbol": "SPY", "option_symbol": "SPY260725C00601000",
            "option_type": "call", "strike_price": "601.0",
            "expiration_date": "2026-07-25", "quantity": "2",
            "average_open_price": "123.00", "type": "long"}]})
        self.assertEqual(len(out), 1)
        o = out[0]
        self.assertEqual(o["Underlying"], "SPY")
        self.assertEqual(o["Strike"], 601.0)
        self.assertEqual(o["Expiration"], date(2026, 7, 25))
        self.assertEqual(o["Contracts"], 2.0)
        self.assertEqual(o["Side"], "long")

    def test_per_contract_premium_is_converted_to_per_share(self):
        """average_open_price is per contract (123.00 = $1.23/share). Storing
        it raw would overstate every options cost basis by 100x."""
        out = broker_sync.normalize_options(
            [{"chain_symbol": "SPY", "quantity": 1, "average_open_price": "123.00"}])
        self.assertEqual(out[0]["Avg_Premium"], 1.23)

    def test_negative_quantity_means_short(self):
        out = broker_sync.normalize_options(
            [{"chain_symbol": "SPY", "quantity": "-3", "average_open_price": "50"}])
        self.assertEqual(out[0]["Side"], "short")
        self.assertEqual(out[0]["Contracts"], 3.0)


class TestMergeOptions(unittest.TestCase):
    def broker_contract(self, **over):
        c = {"Underlying": "SPY", "Option_Symbol": "SPY260725C00601000",
             "Type": "call", "Strike": 601.0, "Expiration": date(2026, 7, 25),
             "Contracts": 2.0, "Side": "long", "Avg_Premium": 1.23,
             "Opened_Date": None}
        c.update(over)
        return c

    def test_quote_is_recorded_with_its_timestamp(self):
        merged, report = broker_sync.merge_options(
            [], [self.broker_contract()],
            premiums={"SPY260725C00601000": 1.80}, synced_at="2026-07-25T10:00:00")
        self.assertEqual(merged[0]["Current_Premium"], 1.80)
        self.assertEqual(merged[0]["Quote_At"], "2026-07-25T10:00:00")
        self.assertEqual(report["added"][0]["contracts"], 2.0)

    def test_unquoted_contract_keeps_its_previous_snapshot(self):
        existing = [{**self.broker_contract(), "Current_Premium": 1.50,
                     "Quote_At": "2026-07-24T15:00:00", "Strategy": "0DTE",
                     "Notes": "day-trade", "_extra": {"Source": "robinhood"}}]
        merged, report = broker_sync.merge_options(existing, [self.broker_contract()])
        self.assertEqual(merged[0]["Current_Premium"], 1.50)
        self.assertEqual(merged[0]["Quote_At"], "2026-07-24T15:00:00")
        self.assertEqual(merged[0]["Strategy"], "0DTE", "user field must survive")
        self.assertEqual(merged[0]["Notes"], "day-trade")
        self.assertEqual(report["unquoted"], [])

    def test_closed_contract_is_dropped_but_reported(self):
        existing = [{**self.broker_contract(), "_extra": {"Source": "robinhood"}}]
        merged, report = broker_sync.merge_options(existing, [])
        self.assertEqual(merged, [])
        self.assertEqual(report["closed"][0]["was_contracts"], 2.0)

    def test_hand_entered_contract_is_kept(self):
        existing = [self.broker_contract(Option_Symbol="MANUAL1")]
        merged, report = broker_sync.merge_options(existing, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(report["manual_untouched"], 1)

    def test_matches_on_contract_tuple_when_symbol_is_missing(self):
        """Without a symbol the identity falls back to the contract's terms —
        otherwise every sync would re-add the same position."""
        existing = [{**self.broker_contract(Option_Symbol=""),
                     "_extra": {"Source": "robinhood"}}]
        merged, report = broker_sync.merge_options(
            existing, [self.broker_contract(Option_Symbol="")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(report["added"], [])


class TestOptionsMath(unittest.TestCase):
    def base(self, **over):
        o = {"Underlying": "SPY", "Option_Symbol": "X", "Type": "call",
             "Strike": 601.0, "Expiration": date(2026, 7, 30), "Contracts": 2.0,
             "Side": "long", "Avg_Premium": 1.00, "Current_Premium": 1.60,
             "Quote_At": "", "Opened_Date": None, "Strategy": "", "Notes": ""}
        o.update(over)
        return o

    def test_long_pnl_applies_the_100x_multiplier(self):
        view = options_positions.build_options_view([self.base()],
                                                    today=date(2026, 7, 25))
        o = view[0]
        self.assertEqual(o["Cost_Basis"], 200.0)     # 1.00 × 100 × 2
        self.assertEqual(o["Market_Value"], 320.0)   # 1.60 × 100 × 2
        self.assertEqual(o["Gain_Dollars"], 120.0)
        self.assertEqual(o["Gain_Pct"], 60.0)

    def test_short_pnl_flips_sign(self):
        """A short that rose in premium is losing money. Reporting it as a
        gain is the single worst thing this math could do."""
        view = options_positions.build_options_view(
            [self.base(Side="short")], today=date(2026, 7, 25))
        o = view[0]
        self.assertEqual(o["Gain_Dollars"], -120.0)
        self.assertEqual(o["Gain_Pct"], -60.0)
        self.assertEqual(o["Market_Value"], -320.0, "a short is a liability")

    def test_expiry_alerts(self):
        today = date(2026, 7, 25)
        zero = options_positions.build_options_view(
            [self.base(Expiration=today)], today=today)[0]
        self.assertEqual(zero["Days_To_Expiry"], 0)
        self.assertTrue(any("0DTE" in a for a in zero["Alerts"]))

        past = options_positions.build_options_view(
            [self.base(Expiration=date(2026, 7, 24))], today=today)[0]
        self.assertTrue(any("expired" in a for a in past["Alerts"]))

    def test_stop_band_alert_fires_at_minus_35(self):
        view = options_positions.build_options_view(
            [self.base(Avg_Premium=2.00, Current_Premium=1.30)], today=date(2026, 7, 25))
        self.assertTrue(any("−35%" in a or "-35%" in a for a in view[0]["Alerts"]))

    def test_view_sorts_soonest_expiry_first(self):
        view = options_positions.build_options_view([
            self.base(Option_Symbol="LATE", Expiration=date(2026, 12, 18)),
            self.base(Option_Symbol="SOON", Expiration=date(2026, 7, 26)),
        ], today=date(2026, 7, 25))
        self.assertEqual([o["Option_Symbol"] for o in view], ["SOON", "LATE"])

    def test_totals_across_contracts(self):
        totals = options_positions.options_totals(
            options_positions.build_options_view([
                self.base(Option_Symbol="A"),
                self.base(Option_Symbol="B", Expiration=date(2026, 7, 26)),
            ], today=date(2026, 7, 25)))
        self.assertEqual(totals["contracts"], 2)
        self.assertEqual(totals["total_cost"], 400.0)
        self.assertEqual(totals["total_gain"], 240.0)
        self.assertEqual(totals["expiring_soon"], 2)

    def test_csv_round_trip(self):
        import tempfile
        rows = [self.base(Option_Symbol="SPY260725C00601000",
                          _extra={"Source": "robinhood"})]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "options_positions.csv"
            options_positions.save_options(rows, path)
            back = options_positions.load_options(path)
        self.assertEqual(back[0]["Option_Symbol"], "SPY260725C00601000")
        self.assertEqual(back[0]["Expiration"], date(2026, 7, 30))
        self.assertEqual(back[0]["Contracts"], 2.0)
        self.assertEqual(back[0]["_extra"]["Source"], "robinhood")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(options_positions.load_options(Path("/nope/none.csv")), [])


if __name__ == "__main__":
    unittest.main()

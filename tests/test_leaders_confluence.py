"""Tests for core.leaders_confluence — the cross-reference between the
pre-market brief and the sector-leader scan.

The conflict cases matter most: they are the ones a single-source read gets
wrong, and they are the reason this layer exists rather than the email simply
printing both engines' output one after the other.
"""

import pytest

from stockanalysis.core import leaders_confluence as LC


def _candidate(ticker, direction, confluence=70.0, group="Semiconductors",
               setup="Lower-high continuation", levels=True):
    return {
        "ticker": ticker, "direction": direction, "group": group,
        "etf": "SMH", "sector_direction": "bearish", "sector_score": 17.0,
        "confluence": {"score": confluence},
        "stock_score": {"score": confluence},
        "leadership": {"score": 75.0, "band": "🟡 Developing leader"},
        "clarity": {"score": 60.0},
        "metrics": {"close": 100.0},
        "setup": {"setup": setup, "grade": "C", "rr": 1.5,
                  "levels": ({"entry_low": 99.0, "entry_high": 101.0,
                              "stop": 105.0, "target1": 92.0,
                              "target2": 88.0} if levels else None)},
    }


def _snap(candidates, sectors=None):
    return {"generated": "2026-08-19T07:00:00-04:00",
            "market": {"label": "STRONG BULLISH", "score": 11.4},
            "candidates": candidates,
            "sectors": sectors or [],
            "confirmations": {}, "verdicts": {}}


def _brief(gainers=(), losers=(), unusual=(), sectors_up=(), sectors_down=(),
           earnings=(), near=()):
    return {"gainers": list(gainers), "losers": list(losers),
            "unusual_movers": list(unusual),
            "sectors_trending": list(sectors_up),
            "sectors_lagging": list(sectors_down),
            "earnings_today": list(earnings), "near_breakout": list(near)}


def test_no_snapshot_is_reported_not_raised():
    out = LC.compute(_brief(), None)
    assert out["available"] is False
    assert "no sector-leader scan snapshot" in out["reason"]
    assert out["aligned"] == [] and out["conflicts"] == []


def test_gainer_that_is_a_bullish_leader_is_aligned():
    snap = _snap([_candidate("DXCM", "long", 83.0, "Healthcare")])
    out = LC.compute(_brief(gainers=[{"ticker": "DXCM", "chg_pct": 2.4}]), snap)
    assert [r["ticker"] for r in out["aligned"]] == ["DXCM"]
    assert out["conflicts"] == []


def test_gainer_that_is_a_bearish_leader_is_a_conflict():
    """MRVL gapped +14.7% into a bearish semiconductor ranking. A read of
    either source alone says 'short it' or 'buy it'; the pair says neither."""
    snap = _snap([_candidate("MRVL", "short", 63.0)])
    out = LC.compute(_brief(gainers=[{"ticker": "MRVL", "chg_pct": 14.7}]), snap)
    assert out["aligned"] == []
    assert out["conflicts"][0]["ticker"] == "MRVL"
    assert "squeeze risk" in out["conflicts"][0]["note"]


def test_loser_that_is_a_bullish_leader_is_a_conflict():
    snap = _snap([_candidate("AMGN", "long", 87.0, "Healthcare")])
    out = LC.compute(_brief(losers=[{"ticker": "AMGN", "chg_pct": -3.2}]), snap)
    assert out["conflicts"][0]["ticker"] == "AMGN"
    assert "thesis" in out["conflicts"][0]["note"]


def test_small_gaps_are_ignored():
    """A 0.4% pre-market print on a thin book is the spread, not a move."""
    snap = _snap([_candidate("DXCM", "long", 83.0, "Healthcare")])
    out = LC.compute(_brief(gainers=[{"ticker": "DXCM", "chg_pct": 0.4}]), snap)
    assert out["aligned"] == [] and out["conflicts"] == []


def test_a_name_in_two_mover_buckets_is_counted_once():
    snap = _snap([_candidate("DXCM", "long", 83.0, "Healthcare")])
    mover = {"ticker": "DXCM", "chg_pct": 4.0}
    out = LC.compute(_brief(gainers=[mover], unusual=[dict(mover)]), snap)
    assert len(out["aligned"]) == 1


def test_extended_names_say_so_instead_of_pricing_the_gap():
    snap = _snap([_candidate("MRK", "long", 95.0, "Healthcare",
                             setup="Extended 4.4 ATR from 20 EMA — no entry",
                             levels=False)])
    out = LC.compute(_brief(gainers=[{"ticker": "MRK", "chg_pct": 6.0}]), snap)
    r = out["aligned"][0]
    assert r["has_entry"] is False
    assert "worse, not better" in r["note"]


def test_sectors_are_matched_on_etf_symbol_not_label():
    """The brief calls XLV "Health" and the scan calls it "Healthcare"."""
    snap = _snap([], sectors=[
        {"name": "Healthcare", "etf": "XLV", "direction": "bullish",
         "scores": {"score": 91.5, "quality_label": "Strong uptrend"}}])
    out = LC.compute(
        _brief(sectors_up=[{"ticker": "XLV", "label": "Health", "chg_pct": 1.6}]),
        snap)
    hit = out["sector_agreement"][0]
    assert hit["scan_name"] == "Healthcare" and hit["premarket_label"] == "Health"
    assert hit["agrees"] is True


def test_sector_disagreement_is_reported_not_dropped():
    snap = _snap([], sectors=[
        {"name": "Technology", "etf": "XLK", "direction": "neutral",
         "scores": {"score": 41.0, "quality_label": "Uptrend pullback"}}])
    out = LC.compute(
        _brief(sectors_down=[{"ticker": "XLK", "label": "Tech", "chg_pct": -2.5}]),
        snap)
    assert out["sector_agreement"][0]["agrees"] is False
    assert out["counts"]["sector_disagree"] == 1


def test_a_confluence_name_reporting_today_is_flagged():
    snap = _snap([_candidate("TGT", "long", 94.0, "Consumer Staples")])
    out = LC.compute(_brief(gainers=[{"ticker": "TGT", "chg_pct": 5.0}],
                            earnings=[{"ticker": "TGT"}]), snap)
    assert [r["ticker"] for r in out["earnings_collisions"]] == ["TGT"]


def test_renderers_survive_an_empty_result():
    out = LC.compute(_brief(), _snap([]))
    assert "none" in LC.render_text(out)
    assert "Sector-leader confluence" in LC.render_html(out)


def test_renderers_survive_a_missing_snapshot():
    out = LC.compute(_brief(), None)
    assert "no sector-leader scan snapshot" in LC.render_text(out)
    assert "no sector-leader scan snapshot" in LC.render_html(out)


class TestSnapshotPersistence:
    """core.leaders_store — what a re-scan is and is not allowed to destroy."""

    @staticmethod
    def _isolate(tmp_path, monkeypatch):
        from stockanalysis.core import leaders_store as store
        monkeypatch.setattr(store, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(store, "SNAPSHOT", tmp_path / "sector_leaders.json")
        return store

    def test_rescan_keeps_verdicts_and_confirmations(self, tmp_path, monkeypatch):
        """A re-scan wiped the stored news readings, so every confidence score
        silently fell back to news 'unavailable' and the table reordered."""
        store = self._isolate(tmp_path, monkeypatch)
        store.save({"candidates": [], "sectors": []},
                   {"NVDA": {"headlines": [{"title": "x"}]}})
        store.save_verdict("NVDA", "long", "confirms", "a real reading")

        store.save({"candidates": [{"ticker": "NVDA"}], "sectors": []})

        snap = store.load()
        assert store.verdict_for(snap, "NVDA", "long")["verdict"] == "confirms"
        assert snap["confirmations"]["NVDA"]["headlines"][0]["title"] == "x"
        assert snap["candidates"] == [{"ticker": "NVDA"}]   # scan half replaced

    def test_confirmations_merge_rather_than_replace(self, tmp_path, monkeypatch):
        store = self._isolate(tmp_path, monkeypatch)
        store.save({"candidates": []}, {"AAA": {"headlines": []}})
        store.save({"candidates": []}, {"BBB": {"headlines": []}})
        assert set(store.load()["confirmations"]) == {"AAA", "BBB"}

    def test_unknown_verdict_is_rejected(self, tmp_path, monkeypatch):
        store = self._isolate(tmp_path, monkeypatch)
        store.save({"candidates": []})
        assert store.save_verdict("NVDA", "long", "bullish-ish") is None

    def test_verdict_needs_a_snapshot_to_write_to(self, tmp_path, monkeypatch):
        store = self._isolate(tmp_path, monkeypatch)
        assert store.save_verdict("NVDA", "long", "confirms") is None

    def test_nan_is_written_as_null_not_nan(self, tmp_path, monkeypatch):
        """json.dumps emits bare NaN, which is not JSON and breaks the reader."""
        import numpy as np
        store = self._isolate(tmp_path, monkeypatch)
        store.save({"candidates": [{"ticker": "X", "rvol": np.float64("nan")}]})
        raw = store.SNAPSHOT.read_text()
        assert "NaN" not in raw
        assert store.load()["candidates"][0]["rvol"] is None

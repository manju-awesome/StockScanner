"""
Tests for the Screener's saved searches (webapp.api).

The invariant: a read that fails must never become a write that replaces the
user's searches with the shipped defaults. That combination — non-atomic
write plus "fall back to starters on parse error" — lost two real saved
searches on 2026-08-05.
Run with: python -m unittest tests.test_saved_screens
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.webapp import api


class SavedScreenCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "saved_screens.json"
        self._orig = api.SAVED_SCREENS_PATH
        api.SAVED_SCREENS_PATH = self.path

    def tearDown(self):
        api.SAVED_SCREENS_PATH = self._orig

    def _write(self, screens):
        self.path.write_text(json.dumps(screens))

    def _names(self):
        return [s["name"] for s in json.loads(self.path.read_text())]


class TestReadSemantics(SavedScreenCase):
    def test_no_file_yet_seeds_the_starters(self):
        self.assertTrue(api.load_saved_screens())

    def test_unreadable_file_does_not_masquerade_as_the_starters(self):
        # The bug: a torn read returned the defaults, and the next save
        # persisted them over the user's own searches.
        self.path.write_text("{ this is not json")
        self.assertIsNone(api._read_saved_screens())
        self.assertEqual(api.load_saved_screens(), [])

    def test_non_list_json_is_unreadable(self):
        self.path.write_text('{"not": "a list"}')
        self.assertIsNone(api._read_saved_screens())


class TestWriteRefusesToClobber(SavedScreenCase):
    def test_save_refuses_when_the_file_cannot_be_read(self):
        self.path.write_text("{ torn")
        res = api.save_screen({"name": "New", "rules": {"op": "AND", "items": []}})
        self.assertFalse(res["ok"])
        self.assertIn("refusing to overwrite", res["message"])
        self.assertEqual(self.path.read_text(), "{ torn")   # untouched

    def test_delete_refuses_when_the_file_cannot_be_read(self):
        self.path.write_text("{ torn")
        res = api.delete_screen("Anything")
        self.assertFalse(res["ok"])
        self.assertEqual(self.path.read_text(), "{ torn")

    def test_a_users_searches_survive_a_save(self):
        self._write([{"name": "Mine", "rules": {"op": "AND", "items": []}}])
        api.save_screen({"name": "Other", "rules": {"op": "AND", "items": []}})
        self.assertEqual(self._names(), ["Mine", "Other"])


class TestSaveAndDelete(SavedScreenCase):
    def test_save_then_load(self):
        self._write([])
        res = api.save_screen({"name": "AI", "rules": {"op": "AND", "items": [
            {"field": "quality", "op": "gt", "value": 90}]}})
        self.assertTrue(res["ok"])
        self.assertIn("AI", self._names())

    def test_saving_the_same_name_replaces_rather_than_duplicates(self):
        self._write([])
        for value in (90, 95):
            api.save_screen({"name": "AI", "rules": {"op": "AND", "items": [
                {"field": "quality", "op": "gt", "value": value}]}})
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["rules"]["items"][0]["value"], 95)

    def test_blank_name_is_rejected(self):
        self._write([])
        self.assertFalse(api.save_screen({"name": "  "})["ok"])

    def test_deleting_an_unknown_name_changes_nothing(self):
        self._write([{"name": "Mine", "rules": {}}])
        res = api.delete_screen("Nope")
        self.assertFalse(res["ok"])
        self.assertEqual(self._names(), ["Mine"])

    def test_delete_removes_only_the_named_one(self):
        self._write([{"name": "A", "rules": {}}, {"name": "B", "rules": {}}])
        self.assertTrue(api.delete_screen("A")["ok"])
        self.assertEqual(self._names(), ["B"])

    def test_names_with_quotes_round_trip(self):
        # The dropdown keys its handlers by index for exactly this case.
        self._write([])
        api.save_screen({"name": "Bill's \"picks\"", "rules": {}})
        self.assertEqual(self._names(), ["Bill's \"picks\""])
        self.assertTrue(api.delete_screen("Bill's \"picks\"")["ok"])


class TestAtomicWrite(SavedScreenCase):
    def test_no_temp_file_is_left_behind(self):
        self._write([])
        api.save_screen({"name": "A", "rules": {}})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_the_file_is_never_observed_empty(self):
        # rename() is atomic, so a concurrent reader sees old or new, never
        # a truncated file mid-write.
        self._write([{"name": "Mine", "rules": {}}])
        api.save_screen({"name": "Other", "rules": {}})
        self.assertIsNotNone(api._read_saved_screens())
        self.assertEqual(len(self._names()), 2)


class TestStarterSeeding(SavedScreenCase):
    def test_deleting_everything_does_not_resurrect_the_starters(self):
        self._write([{"name": "Only", "rules": {}}])
        api.delete_screen("Only")
        self.assertEqual(self._names(), [])
        api.save_screen({"name": "Fresh", "rules": {}})
        self.assertEqual(self._names(), ["Fresh"])

    def test_starters_appear_only_on_a_genuinely_first_run(self):
        self.assertFalse(self.path.exists())
        self.assertTrue(len(api.load_saved_screens()) > 1)
        self._write([])
        self.assertEqual(api.load_saved_screens(), [])


if __name__ == "__main__":
    unittest.main()

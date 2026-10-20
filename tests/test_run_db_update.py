import contextlib
import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from plznito_monitoring import db
from plznito_monitoring import run_db_update as db_update


class RunDbUpdateTests(unittest.TestCase):
    def test_db_restore_reads_item_and_items_snapshots(self):
        with TemporaryDirectory() as tmp_dir:
            data_dir = os.path.join(tmp_dir, "data")
            os.mkdir(data_dir)
            snapshots = {
                "1.json": {"timestamp": 1, "item": {}, "items": [{"id": 1, "name": "cyklostezka"}]},
                "2.json": {"item": {"id": 2, "name": "Cyklo pruh"}},
                "3.json": {"item": {"id": 3, "name": "Díra v silnici"}},
                "4.json": {},
                "notes.txt": "ignored",
            }
            for fname, payload in snapshots.items():
                with open(os.path.join(data_dir, fname), "w", encoding="utf-8") as fw:
                    json.dump(payload, fw)

            db_path = os.path.join(tmp_dir, "plznito.db")
            db_update.db_restore(db_path, data_dirname=data_dir)
            with contextlib.closing(db.connect(db_path)) as conn:
                self.assertEqual([item["id"] for item in db.load_records(conn)], [1, 2, 3])
                self.assertEqual([item["id"] for item in db.load_records(conn, cyklo=True)], [1, 2])
                self.assertIsNotNone(db.last_update(conn))

    def test_resolve_anchor_uses_db_max_id(self):
        anchor, source = db_update._resolve_anchor_id(
            12,
            anchor_id=999,
            seed_data_dir="/definitely/missing",
        )
        self.assertEqual(anchor, 12)
        self.assertEqual(source, "db")

    def test_resolve_anchor_falls_back_to_seed_dir(self):
        with TemporaryDirectory() as tmp_dir:
            for name in ("42260.json", "49553.json", "not_numeric.json", "12.bz2"):
                open(f"{tmp_dir}/{name}", "w", encoding="utf-8").close()

            anchor, source = db_update._resolve_anchor_id(None, seed_data_dir=tmp_dir)

        self.assertEqual(anchor, 49553)
        self.assertEqual(source, f"seed:{tmp_dir}")

    def test_resolve_anchor_errors_when_no_db_seed_or_cli_anchor(self):
        with TemporaryDirectory() as tmp_dir:
            with self.assertRaises(ValueError) as ctx:
                db_update._resolve_anchor_id(None, anchor_id=None, seed_data_dir=tmp_dir)

        self.assertIn("--anchor-id", str(ctx.exception))

    def test_resolve_crawl_range_defaults_and_overrides(self):
        start_default, end_default = db_update._resolve_crawl_range(50000)
        self.assertEqual(
            (start_default, end_default),
            (50000 - db_update.ID_WINDOW_BACK_DEFAULT, 50000 + db_update.ID_LOOKAHEAD_DEFAULT),
        )

        start_override, end_override = db_update._resolve_crawl_range(
            1000,
            id_window_back=10,
            id_lookahead=20,
        )
        self.assertEqual((start_override, end_override), (990, 1020))

    def test_get_plznito_current_data_scrapes_web_only(self):
        db_max_id = 10
        calls = []

        def fake_download(ticket_id, source="auto"):
            calls.append((ticket_id, source))
            return {
                8: {"item": {"id": 8, "name": "eight"}},
                9: {"item": {}},
                10: {},
                11: {"item": {"id": 11, "name": "eleven"}},
            }.get(ticket_id, {})

        with (
            patch.object(db_update, "download_one_id", side_effect=fake_download),
            patch.object(db_update, "scrape_map_page", return_value=[]),
        ):
            payload = db_update.get_plznito_current_data(
                db_max_id,
                id_window_back=2,
                id_lookahead=1,
            )

        self.assertEqual([item["id"] for item in payload["items"]], [8, 11])
        self.assertEqual(sorted(ticket_id for ticket_id, _ in calls), [8, 9, 10, 11])
        self.assertTrue(all(source == "web" for _, source in calls))

    def test_get_plznito_current_data_stops_after_10_consecutive_failures(self):
        db_max_id = 50
        calls = []

        def fake_download(ticket_id, source="auto"):
            calls.append((ticket_id, source))
            return {}

        with (
            patch.object(db_update, "download_one_id", side_effect=fake_download),
            patch.object(db_update, "scrape_map_page", return_value=[]),
        ):
            payload = db_update.get_plznito_current_data(
                db_max_id,
                id_window_back=0,
                id_lookahead=100,
            )

        self.assertEqual(payload["items"], [])
        self.assertEqual(len(calls), db_update.MAX_CONSECUTIVE_SCRAPE_FAILURES)
        self.assertEqual(
            sorted(ticket_id for ticket_id, _ in calls),
            list(range(50, 50 + db_update.MAX_CONSECUTIVE_SCRAPE_FAILURES)),
        )

    def test_get_plznito_current_data_resets_failure_streak_on_success(self):
        db_max_id = 20
        calls = []

        def fake_download(ticket_id, source="auto"):
            calls.append((ticket_id, source))
            if ticket_id == 29:
                return {"item": {"id": 29, "name": "ok"}}
            return {}

        with (
            patch.object(db_update, "download_one_id", side_effect=fake_download),
            patch.object(db_update, "scrape_map_page", return_value=[]),
        ):
            payload = db_update.get_plznito_current_data(
                db_max_id,
                id_window_back=0,
                id_lookahead=25,
            )

        self.assertEqual([item["id"] for item in payload["items"]], [29])
        self.assertEqual(max(ticket_id for ticket_id, _ in calls), 39)
        self.assertEqual(len(calls), 20)

    def test_get_plznito_current_data_reuses_anchor_page(self):
        calls = []

        def fake_download(ticket_id, source="auto"):
            calls.append(ticket_id)
            return {"item": {"id": ticket_id, "name": "fetched"}} if ticket_id == 103 else {}

        anchor_page = [
            {"id": 101, "name": "from page"},
            {"id": 102, "name": "from page"},
            {"id": 105, "name": "from page"},
            {"id": 50, "name": "older than the crawl range"},
        ]
        with (
            patch.object(db_update, "download_one_id", side_effect=fake_download),
            patch.object(db_update, "scrape_map_page", return_value=anchor_page) as mock_page,
        ):
            payload = db_update.get_plznito_current_data(102, id_window_back=1, id_lookahead=5)

        mock_page.assert_called_once_with(102)
        self.assertEqual([item["id"] for item in payload["items"]], [101, 102, 103, 105])
        self.assertEqual(payload["items"][0]["name"], "from page")
        # Only ids missing from the anchor page are downloaded; 50 is before 101..107.
        self.assertEqual(sorted(calls), [103, 104, 106, 107])


if __name__ == "__main__":
    unittest.main()


def _fake_site(existing_ids, listed_ids):
    """download_one_id / scrape_map_page fakes for a site with the given ticket ids."""
    calls = []

    def fake_download(ticket_id, source="auto"):
        calls.append(ticket_id)
        return {"item": {"id": ticket_id, "name": "t"}} if ticket_id in existing_ids else {}

    def fake_page(ticket_id):
        return [{"id": listed_id, "name": "t"} for listed_id in listed_ids]

    return calls, fake_download, fake_page


def test_crawl_catches_up_when_db_is_far_behind():
    # DB ends at 100, the site already has tickets up to 600 (with a gap of deleted ids 300-320).
    existing = set(range(101, 300)) | set(range(321, 601))
    calls, fake_download, fake_page = _fake_site(existing, listed_ids=range(501, 601))
    with (
        patch.object(db_update, "download_one_id", side_effect=fake_download),
        patch.object(db_update, "scrape_map_page", side_effect=fake_page),
    ):
        payload = db_update.get_plznito_current_data(100, id_window_back=0, id_lookahead=20)

    ids = [item["id"] for item in payload["items"]]
    assert ids[0] == 100 or ids[0] == 101
    assert max(ids) == 600
    assert set(ids) >= existing  # nothing missed, the gap of 21 ids did not stop the crawl
    # Ids listed on the anchor page are not downloaded again.
    assert not set(calls) & set(range(501, 601))
    assert payload["stats"]["newest_listed"] == 600
    assert payload["stats"]["requests"] == 1 + len(calls)


def test_crawl_catch_up_is_capped(monkeypatch):
    monkeypatch.setattr(db_update, "MAX_CRAWL_IDS", 50)
    calls, fake_download, fake_page = _fake_site(set(range(1, 10000)), listed_ids=[9999])
    with (
        patch.object(db_update, "download_one_id", side_effect=fake_download),
        patch.object(db_update, "scrape_map_page", side_effect=fake_page),
    ):
        payload = db_update.get_plznito_current_data(100, id_window_back=0, id_lookahead=20)
    assert payload["stats"]["end_id"] == 150
    assert max(item["id"] for item in payload["items"]) == 150


def test_crawl_still_stops_after_failures_beyond_newest_listed():
    calls, fake_download, fake_page = _fake_site(set(range(1, 106)), listed_ids=[105])
    with (
        patch.object(db_update, "download_one_id", side_effect=fake_download),
        patch.object(db_update, "scrape_map_page", side_effect=fake_page),
    ):
        db_update.get_plznito_current_data(100, id_window_back=0, id_lookahead=200)
    # It stops 10 failures after the newest listed id; ids of an already submitted chunk are still fetched.
    stop_id = 105 + db_update.MAX_CONSECUTIVE_SCRAPE_FAILURES
    assert stop_id <= max(calls) < stop_id + db_update.SCRAPE_CHUNK_SIZE


def _db_with(path, records):
    with contextlib.closing(db.connect(path)) as conn:
        db.upsert_records(conn, records)


def _runs(path):
    with contextlib.closing(db.connect(path)) as conn:
        return [(row["ok"], json.loads(row["stats"])) for row in conn.execute("SELECT ok, stats FROM runs ORDER BY id")]


def test_db_update_merges_scraped_tickets_and_records_the_run(tmp_path):
    db_path = str(tmp_path / "plznito.db")
    _db_with(db_path, [{"id": 1, "report": "cyklostezka stary zaznam", "name": "A"}])
    payload = {
        "items": [
            {"id": 2, "report": "kolobezka na stezce", "name": "B"},
            {"id": 3, "report": "bez souvislosti", "name": "C"},
        ],
        "stats": {"anchor": 1},
    }
    with patch.object(db_update, "get_plznito_current_data", return_value=payload) as mocked_get_current:
        db_update.db_update(db_path)

    assert mocked_get_current.call_args.args == (1,)  # the anchor is the DB's highest id
    with contextlib.closing(db.connect(db_path)) as conn:
        assert [item["id"] for item in db.load_records(conn)] == [1, 2, 3]
        assert [item["id"] for item in db.load_records(conn, cyklo=True)] == [1, 2]
    assert _runs(db_path) == [(1, {"mode": "update", "anchor": 1, "new": 2, "updated": 0, "unchanged": 0})]


def test_db_update_failure_keeps_data_and_records_failed_run(tmp_path):
    db_path = str(tmp_path / "plznito.db")
    _db_with(db_path, [{"id": 1, "name": "A"}])
    with (
        patch.object(db_update, "get_plznito_current_data", side_effect=RuntimeError("site down")),
        pytest.raises(RuntimeError),
    ):
        db_update.db_update(db_path)

    with contextlib.closing(db.connect(db_path)) as conn:
        assert db.load_records(conn) == [{"id": 1, "name": "A"}]
        assert db.last_update(conn) is None
    assert _runs(db_path) == [(0, {"mode": "update"})]


def test_db_update_logs_summary_and_warns_when_stale(tmp_path, caplog):
    db_path = str(tmp_path / "plznito.db")
    _db_with(db_path, [{"id": 7, "name": "old", "date": "01.01.2020"}, {"id": 5, "name": "older"}])
    payload = {
        "items": [{"id": 7, "name": "old", "status_id": 3}, {"id": 5, "name": "older"}],
        "stats": {"start_id": 1, "end_id": 9, "anchor": 7, "anchor_source": "db", "requests": 3},
    }
    with (
        patch.object(db_update, "get_plznito_current_data", return_value=payload),
        caplog.at_level("INFO", logger=db_update.logger.name),
    ):
        db_update.db_update(db_path)

    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Update OK")]
    assert len(summary) == 1
    assert (
        "3 HTTP requests" in summary[0]
        and "2 tickets scraped (0 new, 1 updated, 1 unchanged)" in summary[0]
        and "now has 2 records, newest #7 from 01.01.2020" in summary[0]
    )
    assert any(r.levelname == "WARNING" and "No new tickets" in r.getMessage() for r in caplog.records)


def test_import_export_and_backup_round_trip(tmp_path):
    records = [{"id": 2, "name": "Cyklo pruh"}, {"id": 1, "name": "Díra"}]
    json_in = tmp_path / "plznito_all.json"
    json_in.write_text(json.dumps(records), encoding="utf-8")
    db_path = str(tmp_path / "plznito.db")

    db_update.db_import_json(db_path, str(json_in))
    db_update.db_backup(db_path, str(tmp_path / "backup.db"))
    db_update.db_export_json(str(tmp_path / "backup.db"), str(tmp_path / "export.json"))

    exported = json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))
    assert exported == sorted(records, key=lambda record: record["id"])
    assert _runs(db_path) == [(1, {"mode": "import", "new": 2, "updated": 0, "unchanged": 0})]


def test_import_rejects_non_list(tmp_path):
    json_in = tmp_path / "bad.json"
    json_in.write_text(json.dumps({"items": []}), encoding="utf-8")
    with pytest.raises(db_update.InvalidPayloadError):
        db_update.db_import_json(str(tmp_path / "plznito.db"), str(json_in))


@pytest.mark.parametrize("missing_mode", ["export", "backup"])
def test_export_and_backup_need_existing_db(tmp_path, missing_mode):
    db_path = str(tmp_path / "missing.db")
    with pytest.raises(FileNotFoundError):
        if missing_mode == "export":
            db_update.db_export_json(db_path, str(tmp_path / "out.json"))
        else:
            db_update.db_backup(db_path, str(tmp_path / "out.db"))


def test_main_logs_traceback_and_exits_nonzero(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db_update, "LOG_FILE_DEFAULT", str(tmp_path / "update.log"))
    monkeypatch.setattr("sys.argv", ["run_db_update", "--db", str(tmp_path / "plznito.db")])
    with (
        patch.object(db_update, "db_update", side_effect=RuntimeError("site down")),
        pytest.raises(SystemExit) as exit_info,
    ):
        db_update.main()
    assert exit_info.value.code == 1
    assert any(r.levelname == "ERROR" and r.exc_info for r in caplog.records)

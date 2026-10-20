import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from plznito_monitoring import run_db_update as db_update
from plznito_monitoring.common import parse_int


class RunDbUpdateTests(unittest.TestCase):
    def test_filter_data_handles_keywords_and_nulls(self):
        payload = {
            "items": [
                {"id": 1, "report": "Cyklo stezka je rozbita", "name": "A"},
                {"id": 2, "report": "recyklace na ulici", "name": "B"},
                {"id": 3, "report": None, "name": "Kolobezka trasa"},
                {"id": 4, "report": None, "name": None},
                {"id": 5, "name": "CYKLO trasa"},
                "bad-item",
            ]
        }

        result = db_update.filter_data(payload)
        result_ids = [record["id"] for record in result]

        self.assertEqual(result_ids, [1, 3, 5])

    def test_merge_data_replaces_ids_and_skips_malformed_records(self):
        old_records = [
            {"id": 1, "name": "old"},
            {"id": 2, "name": "keep"},
            {"name": "missing-id"},
            {"id": "invalid-id"},
        ]
        new_records = [
            {"id": 1, "name": "new"},
            {"id": "3", "name": "new-three"},
            {"id": None, "name": "invalid"},
            {"other": "missing-id"},
        ]

        merged = db_update.merge_data(old_records, new_records)
        merged_ids = [parse_int(record["id"]) for record in merged]
        merged_by_id = {parse_int(record["id"]): record for record in merged}

        self.assertEqual(merged_ids, [2, 1, 3])
        self.assertEqual(merged_by_id[1]["name"], "new")
        self.assertEqual(merged_by_id[2]["name"], "keep")
        self.assertEqual(merged_by_id[3]["name"], "new-three")

    def test_merge_data_keeps_full_fields_when_scraped_record_arrives(self):
        api_record = {
            "id": 7,
            "name": "Díra",
            "status_id": 2,
            "category_id": 4,
            "address": "Americká 1",
            "created": {"date": "2026-03-01 10:00:00.000000"},
        }
        scraped_record = {
            "id": 7,
            "name": "Díra",
            "status_id": 3,
            "status": "Vyřešené",
            "solution": "Opraveno",
            "address": "",
            "date": "01.03.2026",
        }

        merged = db_update.merge_data([api_record], [scraped_record])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status_id"], 3)
        self.assertEqual(merged[0]["solution"], "Opraveno")
        self.assertEqual(merged[0]["created"], api_record["created"])
        self.assertEqual(merged[0]["category_id"], 4)
        self.assertEqual(merged[0]["address"], "Americká 1")

    def test_merge_data_later_duplicate_wins(self):
        merged = db_update.merge_data([], [{"id": 1, "name": "first"}, {"id": 1, "name": "second"}])
        self.assertEqual(merged, [{"id": 1, "name": "second"}])

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

            for filter_cyklo, expected_ids in ((False, [1, 2, 3]), (True, [1, 2])):
                out_path = os.path.join(tmp_dir, f"out_{filter_cyklo}.json")
                db_update.db_restore(out_path, data_dirname=data_dir, filter_cyklo=filter_cyklo)
                with open(out_path, encoding="utf-8") as fr:
                    restored = json.load(fr)
                self.assertEqual([item["id"] for item in restored], expected_ids)

    def test_validate_payload_rejects_missing_items(self):
        with self.assertRaises(ValueError):
            db_update._validate_payload({})
        with self.assertRaises(ValueError):
            db_update._validate_payload({"items": "not-a-list"})
        with self.assertRaises(ValueError):
            db_update._validate_payload([])

        valid = db_update._validate_payload({"items": []})
        self.assertEqual(valid, {"items": []})

    def test_resolve_anchor_uses_db_max_id(self):
        records = [
            {"id": 7},
            {"id": "12"},
            {"id": None},
            {"name": "missing-id"},
            "bad-item",
        ]

        anchor, source = db_update._resolve_anchor_id(
            records,
            anchor_id=999,
            seed_data_dir="/definitely/missing",
        )
        self.assertEqual(anchor, 12)
        self.assertEqual(source, "db")

    def test_resolve_anchor_falls_back_to_seed_dir(self):
        with TemporaryDirectory() as tmp_dir:
            for name in ("42260.json", "49553.json", "not_numeric.json", "12.bz2"):
                open(f"{tmp_dir}/{name}", "w", encoding="utf-8").close()

            anchor, source = db_update._resolve_anchor_id([], seed_data_dir=tmp_dir)

        self.assertEqual(anchor, 49553)
        self.assertEqual(source, f"seed:{tmp_dir}")

    def test_resolve_anchor_errors_when_no_db_seed_or_cli_anchor(self):
        with TemporaryDirectory() as tmp_dir:
            with self.assertRaises(ValueError) as ctx:
                db_update._resolve_anchor_id([], anchor_id=None, seed_data_dir=tmp_dir)

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
        existing_records = [{"id": 10}]
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
                existing_records,
                id_window_back=2,
                id_lookahead=1,
            )

        self.assertEqual([item["id"] for item in payload["items"]], [8, 11])
        self.assertEqual(sorted(ticket_id for ticket_id, _ in calls), [8, 9, 10, 11])
        self.assertTrue(all(source == "web" for _, source in calls))

    def test_get_plznito_current_data_stops_after_10_consecutive_failures(self):
        existing_records = [{"id": 50}]
        calls = []

        def fake_download(ticket_id, source="auto"):
            calls.append((ticket_id, source))
            return {}

        with (
            patch.object(db_update, "download_one_id", side_effect=fake_download),
            patch.object(db_update, "scrape_map_page", return_value=[]),
        ):
            payload = db_update.get_plznito_current_data(
                existing_records,
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
        existing_records = [{"id": 20}]
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
                existing_records,
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
            payload = db_update.get_plznito_current_data([{"id": 102}], id_window_back=1, id_lookahead=5)

        mock_page.assert_called_once_with(102)
        self.assertEqual([item["id"] for item in payload["items"]], [101, 102, 103, 105])
        self.assertEqual(payload["items"][0]["name"], "from page")
        # Only ids missing from the anchor page are downloaded; 50 is before 101..107.
        self.assertEqual(sorted(calls), [103, 104, 106, 107])

    def test_db_update_writes_all_and_derives_cyklo_without_second_scrape(self):
        with TemporaryDirectory() as tmp_dir:
            all_db_path = os.path.join(tmp_dir, "plznito_all.json")
            cyklo_db_path = os.path.join(tmp_dir, "plznito_cyklo.json")
            with open(all_db_path, "w", encoding="utf-8") as fw:
                json.dump([{"id": 1, "report": "cyklostezka stary zaznam", "name": "A"}], fw)

            payload = {
                "items": [
                    {"id": 2, "report": "kolobezka na stezce", "name": "B"},
                    {"id": 3, "report": "bez souvislosti", "name": "C"},
                ]
            }

            with patch.object(db_update, "get_plznito_current_data", return_value=payload) as mocked_get_current:
                db_update.db_update(
                    all_db_path,
                    filter_cyklo=False,
                    write_cyklo_json_path=cyklo_db_path,
                )

            self.assertEqual(mocked_get_current.call_count, 1)

            with open(all_db_path, encoding="utf-8") as fr:
                all_out = json.load(fr)
            with open(cyklo_db_path, encoding="utf-8") as fr:
                cyklo_out = json.load(fr)

            self.assertEqual([item["id"] for item in all_out], [1, 2, 3])
            self.assertEqual([item["id"] for item in cyklo_out], [1, 2])


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
        payload = db_update.get_plznito_current_data([{"id": 100}], id_window_back=0, id_lookahead=20)

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
        payload = db_update.get_plznito_current_data([{"id": 100}], id_window_back=0, id_lookahead=20)
    assert payload["stats"]["end_id"] == 150
    assert max(item["id"] for item in payload["items"]) == 150


def test_crawl_still_stops_after_failures_beyond_newest_listed():
    calls, fake_download, fake_page = _fake_site(set(range(1, 106)), listed_ids=[105])
    with (
        patch.object(db_update, "download_one_id", side_effect=fake_download),
        patch.object(db_update, "scrape_map_page", side_effect=fake_page),
    ):
        db_update.get_plznito_current_data([{"id": 100}], id_window_back=0, id_lookahead=200)
    # It stops 10 failures after the newest listed id; ids of an already submitted chunk are still fetched.
    stop_id = 105 + db_update.MAX_CONSECUTIVE_SCRAPE_FAILURES
    assert stop_id <= max(calls) < stop_id + db_update.SCRAPE_CHUNK_SIZE


def test_db_update_logs_summary_and_warns_when_stale(tmp_path, caplog):
    db_path = tmp_path / "plznito_all.json"
    db_path.write_text(json.dumps([{"id": 7, "name": "old", "date": "01.01.2020"}]), encoding="utf-8")
    payload = {
        "items": [{"id": 7, "name": "old", "status_id": 3}],
        "stats": {"start_id": 1, "end_id": 9, "anchor": 7, "anchor_source": "db", "requests": 3},
    }
    with (
        patch.object(db_update, "get_plznito_current_data", return_value=payload),
        caplog.at_level("INFO", logger=db_update.logger.name),
    ):
        db_update.db_update(str(db_path), filter_cyklo=False)

    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Update OK")]
    assert len(summary) == 1
    assert (
        "3 HTTP requests" in summary[0]
        and "(0 new, 1 updated)" in summary[0]
        and "newest #7 from 01.01.2020" in summary[0]
    )
    assert any(r.levelname == "WARNING" and "No new tickets" in r.getMessage() for r in caplog.records)


def test_main_logs_traceback_and_exits_nonzero(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db_update, "LOG_FILE_DEFAULT", str(tmp_path / "update.log"))
    monkeypatch.setattr("sys.argv", ["run_db_update", "--db_json", str(tmp_path / "db.json")])
    with (
        patch.object(db_update, "db_update", side_effect=RuntimeError("site down")),
        pytest.raises(SystemExit) as exit_info,
    ):
        db_update.main()
    assert exit_info.value.code == 1
    assert any(r.levelname == "ERROR" and r.exc_info for r in caplog.records)

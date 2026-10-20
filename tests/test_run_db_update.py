import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import run_db_update as db_update
except ModuleNotFoundError as import_error:
    db_update = None
    IMPORT_ERROR = import_error
else:
    IMPORT_ERROR = None


@unittest.skipIf(db_update is None, f"run_db_update import failed: {IMPORT_ERROR}")
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
        merged_ids = [db_update._normalize_id(record["id"]) for record in merged]
        merged_by_id = {db_update._normalize_id(record["id"]): record for record in merged}

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

        with patch.object(db_update, "download_one_id", side_effect=fake_download):
            payload = db_update.get_plznito_current_data(
                existing_records,
                id_window_back=2,
                id_lookahead=1,
            )

        self.assertEqual([item["id"] for item in payload["items"]], [8, 11])
        self.assertEqual([ticket_id for ticket_id, _ in calls], [8, 9, 10, 11])
        self.assertTrue(all(source == "web" for _, source in calls))

    def test_get_plznito_current_data_stops_after_10_consecutive_failures(self):
        existing_records = [{"id": 50}]
        calls = []

        def fake_download(ticket_id, source="auto"):
            calls.append((ticket_id, source))
            return {}

        with patch.object(db_update, "download_one_id", side_effect=fake_download):
            payload = db_update.get_plznito_current_data(
                existing_records,
                id_window_back=0,
                id_lookahead=100,
            )

        self.assertEqual(payload["items"], [])
        self.assertEqual(len(calls), db_update.MAX_CONSECUTIVE_SCRAPE_FAILURES)
        self.assertEqual(
            [ticket_id for ticket_id, _ in calls],
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

        with patch.object(db_update, "download_one_id", side_effect=fake_download):
            payload = db_update.get_plznito_current_data(
                existing_records,
                id_window_back=0,
                id_lookahead=25,
            )

        self.assertEqual([item["id"] for item in payload["items"]], [29])
        self.assertEqual([ticket_id for ticket_id, _ in calls][-1], 39)
        self.assertEqual(len(calls), 20)

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

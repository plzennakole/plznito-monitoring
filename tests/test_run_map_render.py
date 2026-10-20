import json
import os
import tempfile
import unittest
from datetime import datetime

import run_map_render as map_render


def _valid_item(item_id=100, created_date="2025-01-20 12:30:00"):
    return {
        "id": item_id,
        "status_id": 3,
        "category_id": 3,
        "name": "Name",
        "description": "Detailed Description",
        "solution": "Solved text",
        "created": {"date": created_date},
        "latitude": "49.7443392",
        "longitude": "13.3766164",
        "photos": [],
    }


class RunMapRenderTests(unittest.TestCase):
    def test_date_parser_accepts_supported_formats(self):
        self.assertIsNotNone(map_render._parse_date_value("2025-01-20 12:30:00.123456"))
        self.assertIsNotNone(map_render._parse_date_value("2025-01-20 12:30:00"))
        self.assertIsNotNone(map_render._parse_date_value("20.01.2025"))
        self.assertIsNone(map_render._parse_date_value("2025/01/20"))

        date_obj, date_text = map_render._parse_item_datetime({"date": "20.01.2025"})
        self.assertIsNotNone(date_obj)
        self.assertEqual(date_text, "20.01.2025")

    def test_coordinate_parser_rejects_invalid_values(self):
        self.assertEqual(map_render._parse_coordinate("49.12"), 49.12)
        self.assertEqual(map_render._parse_coordinate(13.4), 13.4)
        self.assertIsNone(map_render._parse_coordinate(""))
        self.assertIsNone(map_render._parse_coordinate(" "))
        self.assertIsNone(map_render._parse_coordinate("None"))
        self.assertIsNone(map_render._parse_coordinate("abc"))
        self.assertIsNone(map_render._parse_coordinate(None))

    def test_serialize_map_data_compact_and_skipped(self):
        recent_7 = _valid_item(item_id=100, created_date="2025-01-31 12:30:00")
        recent_30 = _valid_item(item_id=101, created_date="2025-01-20 12:30:00")
        old = _valid_item(item_id=102, created_date="2024-01-20 12:30:00")
        old["category_id"] = 7
        old["status_id"] = 2
        missing_date = _valid_item()
        missing_date.pop("created")
        invalid_coords = _valid_item()
        invalid_coords["latitude"] = "bad"
        missing_required = _valid_item(item_id=103)
        missing_required.pop("status_id")
        records = [recent_7, recent_30, old, missing_date, invalid_coords, missing_required]

        payload = map_render.serialize_map_data(
            records,
            popup_mode="compact",
            now=datetime(2025, 2, 1, 12, 0, 0),
        )

        self.assertEqual(len(payload["markers"]), 3)
        self.assertEqual(payload["stats"]["input_records"], 6)
        self.assertEqual(payload["stats"]["valid_rendered"], 3)
        self.assertEqual(payload["stats"]["added_last_7_days"], 1)
        self.assertEqual(payload["stats"]["added_last_30_days"], 2)
        self.assertEqual(payload["stats"]["added_year_layer"], 1)
        self.assertEqual(payload["stats"]["skipped_total"], 3)
        self.assertEqual(payload["stats"]["skipped_missing_required_fields"], 1)
        self.assertEqual(payload["stats"]["skipped_invalid_date"], 1)
        self.assertEqual(payload["stats"]["skipped_invalid_coordinates"], 1)

        marker = payload["markers"][0]
        self.assertIn("id", marker)
        self.assertIn("status_id", marker)
        self.assertIn("status_label", marker)
        self.assertIn("name", marker)
        self.assertIn("date_text", marker)
        self.assertIn("date_ts", marker)
        self.assertIn("year", marker)
        self.assertIn("lat", marker)
        self.assertIn("lon", marker)
        self.assertIn("category_key", marker)
        self.assertIn("category_label", marker)
        self.assertIn("ticket_url", marker)
        self.assertIn("layer", marker)
        self.assertNotIn("description", marker)
        self.assertNotIn("solution", marker)

        layers = {entry["layer"] for entry in payload["markers"]}
        self.assertIn("recent", layers)
        self.assertIn("2024", layers)
        self.assertEqual(payload["years"], [2024])
        self.assertIsInstance(payload["generated_ts"], int)

        status_options = {entry["key"]: entry["label"] for entry in payload["status_options"]}
        category_options = {entry["key"]: entry["label"] for entry in payload["category_options"]}
        self.assertEqual(status_options["2"], "V řešení")
        self.assertEqual(status_options["3"], "Vyřešeno")
        self.assertEqual(category_options["id:3"], "Kategorie 3")
        self.assertEqual(category_options["id:7"], "Kategorie 7")

        self.assertEqual(len(payload["skipped"]), 3)
        reasons = {entry["reason"] for entry in payload["skipped"]}
        self.assertIn("missing_required_fields", reasons)
        self.assertIn("invalid_date", reasons)
        self.assertIn("invalid_coordinates", reasons)

    def test_serialize_map_data_full_contains_detailed_fields(self):
        record = _valid_item()
        record["photos"] = ["files/photos/thumb_example.jpg"]

        payload = map_render.serialize_map_data(
            [record],
            popup_mode="full",
            now=datetime(2025, 2, 1, 12, 0, 0),
        )

        marker = payload["markers"][0]
        self.assertEqual(marker["description"], "Detailed Description")
        self.assertEqual(marker["solution"], "Solved text")
        self.assertEqual(
            marker["photo_url"],
            "https://tf-prod-plznito-web.s3.eu-central-1.amazonaws.com/files/photos/thumb_example.jpg",
        )
        self.assertEqual(marker["status_label"], "Vyřešeno")
        self.assertEqual(marker["category_key"], "id:3")
        self.assertEqual(marker["category_label"], "Kategorie 3")

    def test_serialize_map_data_category_name_fallback(self):
        record = _valid_item()
        record.pop("category_id")
        record["category"] = "Silnice, cyklostezky"

        payload = map_render.serialize_map_data(
            [record],
            popup_mode="compact",
            now=datetime(2025, 2, 1, 12, 0, 0),
        )

        marker = payload["markers"][0]
        self.assertEqual(marker["category_key"], "name:Silnice, cyklostezky")
        self.assertEqual(marker["category_label"], "Silnice, cyklostezky")
        self.assertEqual(payload["category_options"][0]["key"], "name:Silnice, cyklostezky")

    def test_get_map_compact_excludes_full_text_and_reports_skipped(self):
        valid = _valid_item()
        valid["description"] = "VERY_LONG_DESCRIPTION_SHOULD_NOT_BE_EMBEDDED"
        invalid = _valid_item(item_id=500)
        invalid.pop("status_id")

        with self.assertLogs(map_render.logger, level="INFO") as captured:
            html_output = map_render.get_map([valid, invalid], cluster=True, popup_mode="compact")

        self.assertNotIn("plznito-data-quality", html_output)
        self.assertNotIn("Data quality", html_output)
        self.assertIn('"layer":"2025"', html_output)
        self.assertIn("const statusOptions =", html_output)
        self.assertIn("const categoryOptions =", html_output)
        self.assertNotIn("VERY_LONG_DESCRIPTION_SHOULD_NOT_BE_EMBEDDED", html_output)
        self.assertIn('id="plznito-filter-time"', html_output)
        self.assertIn('id="plznito-filter-reset"', html_output)
        self.assertIn("last_7_days", html_output)
        self.assertIn("function applyFilters()", html_output)
        self.assertIn("plznito-filter-status-", html_output)
        self.assertIn("plznito-filter-category-", html_output)

        log_output = "\n".join(captured.output)
        self.assertIn("Map processing summary:", log_output)
        self.assertIn("added_last_7=", log_output)
        self.assertIn("valid_rendered=1", log_output)
        self.assertIn("skipped_total=1", log_output)

    def test_render_map_to_file_writes_non_empty_html(self):
        valid_a = _valid_item(item_id=100)
        valid_b = {"id": 999, "status_id": 2, "date": "20.01.2025", "latitude": "49.7", "longitude": "13.3"}
        invalid = {"id": 1000, "status_id": 3, "date": "20.01.2025", "latitude": None, "longitude": "13.3"}
        records = [valid_a, valid_b, invalid]

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.json")
            output_path = os.path.join(tmpdir, "nested", "map.html")
            with open(input_path, "w", encoding="utf-8") as fw:
                json.dump(records, fw)

            map_render.render_map_to_file(
                file_in=input_path,
                file_out=output_path,
                cluster=False,
                popup_mode="compact",
            )

            self.assertTrue(os.path.exists(output_path))
            self.assertGreater(os.path.getsize(output_path), 0)

            with open(output_path, encoding="utf-8") as fr:
                html_output = fr.read()

            self.assertIn("plznito-map", html_output)
            self.assertNotIn("Data quality", html_output)
            self.assertEqual(html_output.count('"ticket_url"'), 2)
            self.assertNotIn("Detailed Description", html_output)
            self.assertIn('id="plznito-filter-time"', html_output)
            self.assertIn("function resetFilters()", html_output)

    def test_invalid_popup_mode_raises(self):
        with self.assertRaises(ValueError):
            map_render.serialize_map_data([_valid_item()], popup_mode="invalid")


if __name__ == "__main__":
    unittest.main()

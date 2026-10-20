import json
import os
import re
import tempfile
import unittest
from datetime import datetime

import pytest

from plznito_monitoring import run_map_render as map_render


def _map_config(html_output):
    """Parse the CONFIG object the renderer embeds in the page script."""
    match = re.search(r"const CONFIG = (.*?);\n", html_output)
    assert match, "CONFIG not found in map HTML"
    return json.loads(match.group(1))


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
        self.assertEqual(category_options["id:3"], "Silnice, cyklostezky")
        self.assertEqual(category_options["id:7"], "Dopravní značení")

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
        self.assertEqual(marker["category_label"], "Silnice, cyklostezky")

    def test_serialize_map_data_official_category_name_maps_to_id(self):
        record = _valid_item()
        record.pop("category_id")
        record["category"] = "Silnice, cyklostezky"

        payload = map_render.serialize_map_data(
            [record],
            popup_mode="compact",
            now=datetime(2025, 2, 1, 12, 0, 0),
        )

        marker = payload["markers"][0]
        self.assertEqual(marker["category_key"], "id:3")
        self.assertEqual(marker["category_label"], "Silnice, cyklostezky")
        self.assertEqual(payload["category_options"][0]["key"], "id:3")

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
        self.assertNotIn("VERY_LONG_DESCRIPTION_SHOULD_NOT_BE_EMBEDDED", html_output)
        for element_id in (
            "plznito-filter-period",
            "plznito-filter-status",
            "plznito-filter-category",
            "plznito-filter-reset",
            "plznito-empty",
        ):
            self.assertIn(f'id="{element_id}"', html_output)
        self.assertIn("last_7_days", html_output)
        self.assertIn("function applyFilters()", html_output)

        config = _map_config(html_output)
        self.assertTrue(config["useCluster"])
        self.assertEqual(len(config["markers"]), 1)
        self.assertEqual([option["key"] for option in config["statusOptions"]], ["3"])
        self.assertEqual(set(config["statusColors"]), {"1", "2", "3", "4", "5", "6"})

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
            self.assertIn('id="plznito-filter-period"', html_output)
            self.assertIn("function resetFilters()", html_output)
            self.assertNotIn("__MAP_CONFIG__", html_output)
            self.assertFalse(_map_config(html_output)["useCluster"])

    def test_invalid_popup_mode_raises(self):
        with self.assertRaises(ValueError):
            map_render.serialize_map_data([_valid_item()], popup_mode="invalid")


if __name__ == "__main__":
    unittest.main()


S3_PREFIX = "https://tf-prod-plznito-web.s3.eu-central-1.amazonaws.com/"


@pytest.mark.parametrize(
    "photos, expected",
    [
        (None, None),
        ([], None),
        ([{"thumb": "https://example.org/t.jpg"}], "https://example.org/t.jpg"),
        ([{"thumb": "javascript:alert(1)"}], None),
        ([{"thumb": None}], None),
        (["tickets/1.jpg"], S3_PREFIX + "tickets/1.jpg"),
        (["  "], None),
        (["https://example.org/a.jpg", "b.jpg"], "https://example.org/a.jpg"),
        ([42], None),
    ],
)
def test_sanitize_photo_url(photos, expected):
    assert map_render._sanitize_photo_url({"photos": photos}) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        # Naive dates are Prague local time: CET in winter, CEST in summer.
        (datetime(2025, 1, 1, 1, 0), 1735689600),
        (datetime(2025, 7, 1, 2, 0), 1751328000),
    ],
)
def test_to_unix_timestamp_uses_prague_time(value, expected):
    assert map_render._to_unix_timestamp(value) == expected


def test_api_popup_mode_embeds_no_details_and_points_to_api():
    item = _valid_item()
    payload = map_render.serialize_map_data([item], popup_mode="api", now=datetime(2025, 2, 1, 12, 0, 0))
    marker = payload["markers"][0]
    assert "description" not in marker and "photo_url" not in marker

    html_output = map_render.get_map([item], cluster=True, popup_mode="api")
    config = _map_config(html_output)
    assert config["detailsApiPath"] == "/plznito/api/issues/"
    assert config["popupMode"] == "api"


def test_unknown_popup_mode_is_rejected():
    with pytest.raises(ValueError):
        map_render.serialize_map_data([], popup_mode="huge")


def _scraped(item_id, date_text, category="Silnice, cyklostezky"):
    return {
        "id": item_id,
        "name": "x",
        "status_id": 3,
        "latitude": "49.7",
        "longitude": "13.3",
        "category": category,
        "date": date_text,
    }


@pytest.mark.parametrize(
    "items, expected",
    [
        # A ticket closed late gets the earlier date of a newer ticket.
        ([_scraped(1, "20.03.2026"), _scraped(2, "02.01.2026")], {1: datetime(2026, 1, 2), 2: datetime(2026, 1, 2)}),
        # Its own date when no newer ticket is earlier.
        ([_scraped(1, "05.01.2026"), _scraped(2, "07.01.2026")], {1: datetime(2026, 1, 5), 2: datetime(2026, 1, 7)}),
        # Real API creation dates are used as bounds but never estimated.
        (
            [_scraped(1, "20.03.2026"), {**_valid_item(item_id=2), "created": {"date": "2026-01-03 08:00:00"}}],
            {1: datetime(2026, 1, 3, 8, 0)},
        ),
        # Unsorted input and junk records.
        (
            [_scraped(2, "02.01.2026"), "junk", {"id": None}, _scraped(1, "20.03.2026")],
            {1: datetime(2026, 1, 2), 2: datetime(2026, 1, 2)},
        ),
    ],
)
def test_estimate_creation_dates(items, expected):
    assert map_render.estimate_creation_dates(items) == expected


def test_estimate_creation_dates_window_limits_an_outlier():
    # A mistyped old date only affects the CREATION_WINDOW tickets before it.
    window = map_render.CREATION_WINDOW
    items = [_scraped(i, "01.03.2026") for i in range(1, 2 * window + 1)]
    items.append(_scraped(2 * window + 1, "01.01.2020"))
    estimates = map_render.estimate_creation_dates(items)
    assert estimates[window] == datetime(2026, 3, 1)
    assert estimates[window + 1] == datetime(2020, 1, 1)


def test_recently_closed_old_ticket_is_not_in_last_30_days():
    # Ticket 1 was created in January and closed on 20 March: its scraped date is the close date.
    items = [_scraped(1, "20.03.2026"), _scraped(2, "05.01.2026"), _scraped(3, "21.03.2026")]
    payload = map_render.serialize_map_data(items, now=datetime(2026, 3, 25, 12, 0))
    layers = {marker["id"]: marker["layer"] for marker in payload["markers"]}
    assert layers == {"1": "2026", "2": "2026", "3": "recent"}
    assert payload["markers"][0]["date_text"] == "≈ 05.01.2026"


def test_categories_of_both_record_shapes_are_merged():
    api_record = {**_valid_item(item_id=1), "category_id": 3}
    payload = map_render.serialize_map_data(
        [api_record, _scraped(2, "05.01.2026"), _scraped(3, "05.01.2026", category="Něco nového")],
        now=datetime(2026, 3, 25, 12, 0),
    )
    assert [(o["key"], o["label"]) for o in payload["category_options"]] == [
        ("id:3", "Silnice, cyklostezky"),
        ("name:Něco nového", "Něco nového"),
    ]


@pytest.mark.parametrize(
    "template_text", ["<script>no placeholder</script>", "/*__MAP_CONFIG__*/null /*__MAP_CONFIG__*/null"]
)
def test_map_template_needs_exactly_one_placeholder(tmp_path, monkeypatch, template_text):
    template = tmp_path / "template.html"
    template.write_text(template_text, encoding="utf-8")
    monkeypatch.setattr(map_render, "MAP_TEMPLATE_PATH", str(template))
    with pytest.raises(ValueError):
        map_render.get_map([_valid_item()])


def test_map_config_cannot_break_out_of_the_script():
    item = {**_valid_item(), "name": "</script><script>alert(1)</script>"}
    html_output = map_render.get_map([item])
    assert "</script><script>alert(1)" not in html_output
    assert _map_config(html_output)["markers"][0]["name"] == item["name"]

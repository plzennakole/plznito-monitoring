import unittest

import pytest

from plznito_monitoring import cyklo_filter, restore_all
from plznito_monitoring import run_db_update as db_update


class CykloFilterTests(unittest.TestCase):
    def test_filter_cyklo_items_handles_keywords_exclusions_and_malformed_records(self):
        items = [
            {"id": 1, "report": "Cyklostezka rozbita", "name": "A"},
            {"id": 2, "report": None, "name": "Kolobezka trasa"},
            {"id": 3, "report": "recyklace u stezky", "name": "CYKLO trasa"},
            {"id": 4, "report": None, "name": None},
            {"id": 5},
            "bad-item",
            {"id": 6, "report": "bez souvislosti", "name": "CYKLO trasa"},
            {"id": 7, "report": "Cikli problem", "name": ""},
        ]

        result = cyklo_filter.filter_cyklo_items(items)
        result_ids = [record["id"] for record in result]

        self.assertEqual(result_ids, [1, 2, 6, 7])

    def test_to_lower_text_handles_none_and_non_strings(self):
        self.assertEqual(cyklo_filter.to_lower_text(None), "")
        self.assertEqual(cyklo_filter.to_lower_text("CYKLO"), "cyklo")
        self.assertEqual(cyklo_filter.to_lower_text(123), "123")

    def test_is_cyklo_record_checks_report_or_name(self):
        self.assertTrue(cyklo_filter.is_cyklo_record("cyklostezka", ""))
        self.assertTrue(cyklo_filter.is_cyklo_record("", "kolobezka"))
        self.assertFalse(cyklo_filter.is_cyklo_record("auto", "chodnik"))


class FilterParityTests(unittest.TestCase):
    def test_restore_and_update_filter_data_return_same_ids(self):
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

        restore_result = restore_all.filter_data(payload)
        update_result = db_update.filter_data(payload)

        restore_ids = [record["id"] for record in restore_result]
        update_ids = [record["id"] for record in update_result]
        self.assertEqual(restore_ids, update_ids)


@pytest.mark.parametrize("item, expected", [
    ({"report": "Cyklostezka rozbita", "name": "A"}, True),
    ({"report": None, "name": "Kolobezka trasa"}, True),
    ({"report": "recyklace u stezky", "name": "CYKLO trasa"}, False),
    ({"report": "bez souvislosti", "name": "CYKLO trasa"}, True),
    ({"report": "bez souvislosti", "name": "Díra"}, False),
    ({"report": None, "name": None}, False),
    ({}, False),
    ("bad-item", False),
])
def test_is_cyklo_item(item, expected):
    assert cyklo_filter.is_cyklo_item(item) is expected


if __name__ == "__main__":
    unittest.main()

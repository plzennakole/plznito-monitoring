import contextlib
import gzip
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


if __name__ == "__main__":
    unittest.main()


def _fake_site(existing_ids, listed_ids):
    """download_recent / download_one_id fakes for a site with the given ticket ids."""
    calls = []

    def fake_download(ticket_id):
        calls.append(ticket_id)
        return {"item": {"id": ticket_id, "name": "detail"}} if ticket_id in existing_ids else {}

    def fake_list(recent_days):
        return [{"id": listed_id, "name": "listed"} for listed_id in listed_ids]

    return calls, fake_download, fake_list


def _current_data(existing_ids, listed_ids, db_max_id, id_window_back=0):
    calls, fake_download, fake_list = _fake_site(existing_ids, listed_ids)
    with (
        patch.object(db_update, "download_one_id", side_effect=fake_download),
        patch.object(db_update, "download_recent", side_effect=fake_list),
    ):
        payload = db_update.get_plznito_current_data(db_max_id, id_window_back=id_window_back)
    return payload, calls


def test_current_data_uses_list_and_downloads_only_missing_ids():
    # DB ends at 100; the list has 98-99 (older, refreshed) and 103-105; 101 exists, 102 was deleted.
    payload, calls = _current_data({101}, listed_ids=[98, 99, 103, 104, 105], db_max_id=100, id_window_back=2)

    assert sorted(calls) == [100, 101, 102]
    assert [item["id"] for item in payload["items"]] == [98, 99, 101, 103, 104, 105]
    assert payload["items"][2]["name"] == "detail"
    assert payload["stats"] == {
        "start_id": 98,
        "end_id": 105,
        "listed": 5,
        "newest_listed": 105,
        "requests": 4,
        "empty": 2,
    }


def test_current_data_empty_db_uses_only_the_list():
    payload, calls = _current_data(set(), listed_ids=[10, 12], db_max_id=None)
    assert sorted(calls) == [11]
    assert [item["id"] for item in payload["items"]] == [10, 12]


def test_current_data_catches_up_when_db_is_far_behind(caplog):
    # DB ends at 100, the site has tickets up to 600 (deleted ids 300-320), the list has the newest 100.
    existing = set(range(101, 300)) | set(range(321, 601))
    with caplog.at_level("WARNING", logger=db_update.logger.name):
        payload, calls = _current_data(existing, listed_ids=range(501, 601), db_max_id=100)

    ids = [item["id"] for item in payload["items"]]
    assert set(ids) == existing
    assert not set(calls) & set(range(501, 601))  # listed tickets are not downloaded again
    assert payload["stats"]["empty"] == 1 + 21  # id 100 (not in the fake site) and the deleted gap
    assert any("tickets behind plznito.cz" in r.getMessage() for r in caplog.records)


def test_current_data_catch_up_is_capped_without_skipping_ids(monkeypatch):
    monkeypatch.setattr(db_update, "MAX_CRAWL_IDS", 50)
    payload, calls = _current_data(set(range(1, 10000)), listed_ids=[9998, 9999], db_max_id=100)

    assert payload["stats"]["end_id"] == 150
    # Listed tickets beyond the cap wait for the next run, so the DB's max id stays contiguous.
    assert max(item["id"] for item in payload["items"]) == 150
    assert max(calls) == 150


def test_current_data_empty_list_fails_loudly():
    with (
        patch.object(db_update, "download_recent", return_value=[]),
        patch.object(db_update, "download_one_id") as mock_download,
        pytest.raises(db_update.PlznitoApiError, match="returned no tickets"),
    ):
        db_update.get_plznito_current_data(100)
    mock_download.assert_not_called()


def test_current_data_rejects_negative_window():
    with pytest.raises(ValueError):
        db_update.get_plznito_current_data(100, id_window_back=-1)


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
        "stats": {"start_id": 1},
    }
    with patch.object(db_update, "get_plznito_current_data", return_value=payload) as mocked_get_current:
        db_update.db_update(db_path)

    assert mocked_get_current.call_args.args == (1,)  # the DB's highest id
    with contextlib.closing(db.connect(db_path)) as conn:
        assert [item["id"] for item in db.load_records(conn)] == [1, 2, 3]
        assert [item["id"] for item in db.load_records(conn, cyklo=True)] == [1, 2]
    assert _runs(db_path) == [(1, {"mode": "update", "start_id": 1, "new": 2, "updated": 0, "unchanged": 0})]


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
        "stats": {"start_id": 1, "end_id": 9, "listed": 2, "requests": 3},
    }
    with (
        patch.object(db_update, "get_plznito_current_data", return_value=payload),
        caplog.at_level("INFO", logger=db_update.logger.name),
    ):
        db_update.db_update(db_path)

    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Update OK")]
    assert len(summary) == 1
    assert (
        "2 listed, 3 HTTP requests" in summary[0]
        and "2 tickets downloaded (0 new, 1 updated, 1 unchanged)" in summary[0]
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


def test_gzipped_backup_restores_to_the_same_records(tmp_path):
    db_path = str(tmp_path / "plznito.db")
    with contextlib.closing(db.connect(db_path)) as conn:
        db.upsert_records(conn, [{"id": 1, "name": "Cyklo pruh"}])
    backup_path = tmp_path / "plznito-3.db.gz"
    backup_path.write_bytes(b"old backup")

    db_update.db_backup(db_path, str(backup_path))

    restored = tmp_path / "restored.db"
    with gzip.open(backup_path, "rb") as fr:
        restored.write_bytes(fr.read())
    with contextlib.closing(db.connect(restored, create=False)) as conn:
        assert db.load_records(conn) == [{"id": 1, "name": "Cyklo pruh"}]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plznito-3.db.gz", "plznito.db", "restored.db"]


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

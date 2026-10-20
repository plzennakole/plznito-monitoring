# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import pathlib

import pytest

from plznito_monitoring import common


@pytest.mark.parametrize(
    'value, expected',
    [
        (5, 5),
        (0, 0),
        ('42', 42),
        (' 7 ', 7),
        ('-1', None),
        ('4.2', None),
        ('abc', None),
        ('', None),
        (None, None),
        (True, None),
        (4.0, None),
    ],
)
def test_parse_int(value: object, expected: int | None) -> None:
    assert common.parse_int(value) == expected


@pytest.mark.parametrize('status_id, expected', [(1, "Nové"), (6, "Odpovězeno"), (42, "Stav 42")])
def test_status_label(status_id: int, expected: str) -> None:
    assert common.status_label(status_id) == expected


def test_atomic_write_json_roundtrip_and_replace(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'nested' / 'db.json'
    common.atomic_write_json(str(path), [{'id': 1}], indent=4)
    common.atomic_write_json(str(path), [{'id': 2, 'name': "Díra"}])

    assert common.load_json(str(path)) == [{'id': 2, 'name': "Díra"}]
    assert path.read_text(encoding='utf-8') == '[{"id":2,"name":"Díra"}]'
    assert [p.name for p in path.parent.iterdir()] == ['db.json']


def test_atomic_open_keeps_old_file_on_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'map.html'
    path.write_text("old", encoding='utf-8')

    with pytest.raises(RuntimeError), common.atomic_open(str(path)) as fw:
        fw.write("half")
        raise RuntimeError("boom")

    assert path.read_text(encoding='utf-8') == "old"
    assert [p.name for p in tmp_path.iterdir()] == ['map.html']

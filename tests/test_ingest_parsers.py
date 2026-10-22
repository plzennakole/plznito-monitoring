# Copyright 2026 Plzeň na kole Inc. All rights reserved.
from datetime import date

import pytest
from pytest_mock import MockerFixture

from bikecounters_web import ingest


@pytest.mark.parametrize(
    'header',
    [
        'siteId;timestamp;bike_count_in;bike_count_out;scooter_count_in;scooter_count_out',
        # Site column first used to be treated as missing (index 0 is falsy).
        'timestamp;site_id;bike_count_in;bike_count_out;scooter_count_in;scooter_count_out',
    ],
)
def test_parse_ecocounter_csv(header: str) -> None:
    columns = header.split(';')
    rows = [
        {
            'siteid': '7',
            'site_id': '7',
            'timestamp': '2025-06-01T08:15:00Z',
            'bike_count_in': '3',
            'bike_count_out': '4',
            'scooter_count_in': '1',
            'scooter_count_out': '',
        },
        {
            'siteid': '7',
            'site_id': '7',
            'timestamp': 'not-a-date',
            'bike_count_in': '1',
            'bike_count_out': '1',
            'scooter_count_in': '0',
            'scooter_count_out': '0',
        },
        {
            'siteid': '7',
            'site_id': '7',
            'timestamp': '2025-06-01T08:30:00Z',
            'bike_count_in': 'x',
            'bike_count_out': '1',
            'scooter_count_in': '0',
            'scooter_count_out': '0',
        },
    ]
    lines = [header] + [';'.join(row[col.lower()] for col in columns) for row in rows] + ['short;line']

    assert ingest.parse_ecocounter_csv('\n'.join(lines)) == (
        [('eco_7_in', '2025-06-01 08:15:00', 3, 1)],
        [('eco_7_out', '2025-06-01 08:15:00', 4, 0)],
    )


def test_parse_ecocounter_csv_missing_columns() -> None:
    assert ingest.parse_ecocounter_csv('foo;bar\n1;2') is None


def test_parse_ecocounter_csv_empty() -> None:
    assert ingest.parse_ecocounter_csv('') == ([], [])


@pytest.mark.parametrize(
    'text, expected',
    [
        (
            "ID kolektoru\tZačátek intervalu\tJízdní kola\tKoloběžky\n"
            "1\t30.6.2025 23:58\t5\t2\n"
            "2\t2025-07-01T00:13:00\t1\t\n"
            "bad\trow\n",
            [('cam_9_c1', '2025-06-30 23:58:00', 5, 2), ('cam_9_c2', '2025-07-01 00:13:00', 1, 0)],
        ),
        (
            "ID kolektoru;Začátek intervalu;Jízdní kola;Koloběžky\n3;1.1.2025 10:00;7;0\n",
            [('cam_9_c3', '2025-01-01 10:00:00', 7, 0)],
        ),
        ("a\tb\tc\td\n1\t2\t3\t4\n", []),
        ("", []),
    ],
)
def test_parse_camera_csv(text: str, expected: list[tuple]) -> None:
    assert ingest._parse_camera_csv(text, '9') == expected


@pytest.mark.parametrize(
    'day, expected',
    [
        (date(2025, 1, 15), date(2025, 2, 1)),
        (date(2025, 12, 31), date(2026, 1, 1)),
    ],
)
def test_next_month(day: date, expected: date) -> None:
    assert ingest._next_month(day) == expected


@pytest.mark.parametrize(
    'text, expected',
    [
        # Real ČHMÚ historical format.
        (
            'STATION,ELEMENT,TIMEFUNC,DT,VALUE,FLAG,QUALITY,\n'
            '0-20000-0-11450,T,AVG,2004-07-01T00:00Z,18.2,,0.0,\n'
            '0-20000-0-11450,T,AVG,2004-07-02T00:00Z,-999,,0.0,\n'
            '0-20000-0-11450,T,AVG,bad,1,,0.0,\n',
            {'2004-07-01': 18.2, '2004-07-02': None},
        ),
        ('rok;mesic;den;T\n2025;6;3;14.1\n2025;6;4;\n', {'2025-06-03': 14.1, '2025-06-04': None}),
        ('foo,bar\n1,2\n', {}),
        ('', {}),
    ],
)
def test_chmi_parse_csv(text: str, expected: dict) -> None:
    assert ingest._chmi_parse_csv(text, 'T') == expected


def test_chmi_parse_recent_json() -> None:
    data = {
        'data': {
            'data': {
                'header': 'STATION,ELEMENT,VTYPE,DT,VAL',
                'values': [
                    ['11450', 'T', 'AVG', '2025-06-01T00:00:00Z', '18.2'],
                    ['11450', 'T', 'MAX', '2025-06-01T00:00:00Z', '25.0'],
                    ['11450', 'SRA', 'SUM', '2025-06-01T00:00:00Z', '-999'],
                    ['11450', 'SRA', 'SUM', '2025-06-02T00:00:00Z', '3.4'],
                    ['too', 'short'],
                ],
            }
        }
    }
    assert ingest._chmi_parse_recent_json(data) == {
        '2025-06-01': {'t': 18.2, 'p': None},
        '2025-06-02': {'p': 3.4},
    }


@pytest.mark.parametrize('data', [{}, None, {'data': {'data': {'header': 'A,B', 'values': []}}}])
def test_chmi_parse_recent_json_unexpected_shape(data: object) -> None:
    assert ingest._chmi_parse_recent_json(data) == {}


@pytest.mark.parametrize(
    'body',
    [
        b'Request OK',
        b'<html><body>Maintenance</body></html>',
    ],
)
def test_fetch_rejects_non_csv(mocker: MockerFixture, body: bytes) -> None:
    mocker.patch('requests.get', return_value=mocker.Mock(content=body))
    with pytest.raises(ingest.NotCsvError, match="non-CSV"):
        ingest.fetch('https://example.invalid/data')


@pytest.mark.parametrize(
    'body',
    [
        b'siteId;timestamp\n7;2025-06-01',
        b'ID kolektoru\tZ\xc3\xa1\xc4\x8d\xc3\xa1tek intervalu\n5\t1.6.2025 08:00',
        b'a,b\n1,2',
        b'',
    ],
)
def test_fetch_accepts_csv(mocker: MockerFixture, body: bytes) -> None:
    mocker.patch('requests.get', return_value=mocker.Mock(content=body))
    assert ingest.fetch('https://example.invalid/data') == body.decode('utf-8')


def test_ingest_ecocounter_non_csv_returns_zero(mocker: MockerFixture) -> None:
    mocker.patch.object(ingest, 'fetch', side_effect=ingest.NotCsvError("down"))
    assert ingest.ingest_ecocounter() == 0


@pytest.mark.parametrize(
    ('argv', 'eco_rows', 'cam_rows', 'exit_code'),
    [
        (['--no-weather'], 0, 0, 1),
        (['--no-weather'], 10, 0, None),
        (['--no-weather'], 0, 5, None),
        (['--source', 'cam'], 0, 0, 1),
        (['--source', 'weather'], 0, 0, None),
    ],
)
def test_main_exit_code(
    mocker: MockerFixture, argv: list[str], eco_rows: int, cam_rows: int, exit_code: int | None
) -> None:
    mocker.patch('sys.argv', ['ingest', *argv])
    mocker.patch.object(ingest.shutil, 'disk_usage', return_value=mocker.Mock(free=10**12))
    mocker.patch.object(ingest, 'init_db')
    mocker.patch.object(ingest, 'checkpoint_db')
    mocker.patch.object(ingest, 'ingest_weather')
    mocker.patch.object(ingest, 'ingest_ecocounter', return_value=eco_rows)
    mocker.patch.object(ingest, 'ingest_camera', return_value=cam_rows)

    if exit_code is None:
        ingest.main()
    else:
        with pytest.raises(SystemExit) as exc:
            ingest.main()
        assert exc.value.code == exit_code

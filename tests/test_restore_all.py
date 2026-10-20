# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import json
import pathlib
from unittest.mock import MagicMock

import pytest
import requests
from pytest_mock import MockerFixture

from plznito_monitoring import restore_all

MAP_PAGE = """
<html><script>
  var map = L.map('map');
  var locations = [
    {id: 100, name: 'Díra', description: 'Velká díra', lat: 49.7, lng: 13.3, status_id: 2, date: '01.03.2026'},
    {id: 101, name: 'Lampa', description: 'Nesvítí', status_id: 3,},
  ];
  render(locations);
</script></html>
"""


def _response(text: str = "", json_data: object = None, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.json.return_value = json_data
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return response


def test_parse_locations_from_html_reads_json5() -> None:
    locations = restore_all._parse_locations_from_html(MAP_PAGE)
    assert [location['id'] for location in locations] == [100, 101]


def test_parse_locations_from_html_without_locations() -> None:
    assert restore_all._parse_locations_from_html("<html></html>") == []


def test_parse_locations_from_html_invalid_json5() -> None:
    with pytest.raises(ValueError):
        restore_all._parse_locations_from_html("var locations = [{id: }];")


def test_location_to_item_maps_fields() -> None:
    item = restore_all._location_to_item(restore_all._parse_locations_from_html(MAP_PAGE)[0])
    assert item == {
        'id': 100,
        'name': "Díra",
        'report': "Velká díra",
        'description': "Velká díra",
        'solution': None,
        'latitude': '49.7',
        'longitude': '13.3',
        'status': None,
        'status_id': 2,
        'category': None,
        'address': '',
        'photos': [],
        'date': '01.03.2026',
    }


def test_location_to_item_without_coordinates() -> None:
    item = restore_all._location_to_item({'id': 1})
    assert (item['latitude'], item['longitude']) == ('', '')


@pytest.mark.parametrize('ticket_id, response, expected_id', [
    (100, _response(MAP_PAGE), 100),
    (101, _response(MAP_PAGE), 101),
    (999, _response(MAP_PAGE), None),
    (100, _response(status=500), None),
    (100, _response("var locations = [{id: }];"), None),
])
def test_scrape_one_id_from_map(
    mocker: MockerFixture, ticket_id: int, response: MagicMock, expected_id: int | None,
) -> None:
    mock_get = mocker.patch.object(restore_all.requests, 'get', return_value=response)

    result = restore_all.scrape_one_id_from_map(ticket_id)

    assert mock_get.call_args.args[0] == f"https://www.plznito.cz/map/{ticket_id}"
    assert (result['item']['id'] if result else None) == expected_id


def test_scrape_one_id_from_map_network_error(mocker: MockerFixture) -> None:
    mocker.patch.object(restore_all.requests, 'get', side_effect=requests.ConnectionError("down"))
    assert restore_all.scrape_one_id_from_map(100) == {}


@pytest.mark.parametrize('source, api_response, expected, expect_scrape', [
    ('auto', _response(json_data={'item': {'id': 1}}), {'item': {'id': 1}}, False),
    ('auto', _response(json_data={}), {'item': {'id': 9}}, True),
    ('auto', _response(status=404), {'item': {'id': 9}}, True),
    ('api', _response(status=404), {}, False),
    ('api', _response(json_data={}), {}, False),
    ('web', None, {'item': {'id': 9}}, True),
])
def test_download_one_id_sources(
    mocker: MockerFixture, source: str, api_response: MagicMock | None, expected: dict, expect_scrape: bool,
) -> None:
    mock_get = mocker.patch.object(restore_all.requests, 'get', return_value=api_response)
    mock_scrape = mocker.patch.object(restore_all, 'scrape_one_id_from_map', return_value={'item': {'id': 9}})

    assert restore_all.download_one_id(1, source=source) == expected
    assert mock_scrape.called is expect_scrape
    assert mock_get.called is (source != 'web')


def test_download_one_id_invalid_api_json_falls_back(mocker: MockerFixture) -> None:
    response = _response()
    response.json.side_effect = json.JSONDecodeError("bad", "", 0)
    mocker.patch.object(restore_all.requests, 'get', return_value=response)
    mocker.patch.object(restore_all, 'scrape_one_id_from_map', return_value={'item': {'id': 9}})

    assert restore_all.download_one_id(1) == {'item': {'id': 9}}


def test_download_range_skips_existing_and_failed(mocker: MockerFixture, tmp_path: pathlib.Path) -> None:
    (tmp_path / '1.json').write_text('{"item": {"id": 1}}', encoding='utf-8')
    downloads = {2: {'item': {'id': 2}}, 3: {}}
    mock_download = mocker.patch.object(restore_all, 'download_one_id', side_effect=lambda i, source: downloads[i])

    restore_all.download_range(str(tmp_path), 1, 3, 'web')

    assert [call.args[0] for call in mock_download.call_args_list] == [2, 3]
    assert sorted(p.name for p in tmp_path.iterdir()) == ['1.json', '2.json']
    assert json.loads((tmp_path / '2.json').read_text(encoding='utf-8')) == {'item': {'id': 2}}

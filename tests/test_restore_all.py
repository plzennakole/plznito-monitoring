# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import json
import pathlib
from unittest.mock import MagicMock

import pytest
import requests
from pytest_mock import MockerFixture

from plznito_monitoring import restore_all

# Shortened ticket as returned by /api/3.0/ticket/detail/<id> (the list has the same items without viewer fields).
API_TICKET = {
    'id': 55394,
    'name': "Nepořádek, skládka",
    'status_id': 5,
    'category_id': 2,
    'description': "Prosím o úklid.",
    'solution': None,
    'longitude': '13.354400508105755',
    'latitude': '49.78126037784341',
    'created': {'date': '2026-10-20 15:41:58.000000', 'timezone_type': 3, 'timezone': 'Europe/Prague'},
    'address': "Chotíkov 338",
    'urgency': {'can_urge': False, 'urged_at': None, 'reason': 'forbidden'},
    'editable': {'can_edit': False, 'reason': 'forbidden'},
    'category_name': "Ostatní",
    'status_name': "Nepatří městu",
    'photos': [{'id': 1, 'thumb': 'https://example.com/thumb.jpeg'}],
}


def _response(json_data: object = None, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = json_data
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return response


def test_normalize_api_item_drops_viewer_fields_and_adds_report() -> None:
    record = restore_all.normalize_api_item(API_TICKET)

    assert 'urgency' not in record and 'editable' not in record
    assert record['report'] == "Prosím o úklid."
    assert record['created'] == API_TICKET['created']
    assert 'urgency' in API_TICKET  # the input is not modified


@pytest.mark.parametrize(
    'item, expected_report',
    [
        ({'id': 1, 'report': "starý text", 'description': "nový"}, "starý text"),
        ({'id': 1, 'description': None}, ""),
        ({'id': 1}, ""),
    ],
)
def test_normalize_api_item_report(item: dict, expected_report: str) -> None:
    assert restore_all.normalize_api_item(item)['report'] == expected_report


def test_download_one_id(mocker: MockerFixture) -> None:
    mock_get = mocker.patch.object(
        restore_all.SESSION, 'get', return_value=_response({'timestamp': 1, 'item': API_TICKET})
    )

    result = restore_all.download_one_id(55394)

    assert mock_get.call_args.args[0] == "https://www.plznito.cz/api/3.0/ticket/detail/55394"
    assert result['item']['id'] == 55394
    assert result['item']['report'] == "Prosím o úklid."
    assert 'urgency' not in result['item']


@pytest.mark.parametrize('status', [503, 404])
def test_download_one_id_missing_ticket(mocker: MockerFixture, status: int) -> None:
    # The API's answer for deleted and archived tickets: an expected gap, not an error.
    mocker.patch.object(restore_all.SESSION, 'get', return_value=_response(status=status))
    assert restore_all.download_one_id(1) == {}


@pytest.mark.parametrize(
    'response, error',
    [
        (_response(status=500), requests.HTTPError),
        (_response(status=429), requests.HTTPError),
        (_response({'item': None}), restore_all.PlznitoApiError),
        (_response({'item': {}}), restore_all.PlznitoApiError),
        (_response([]), restore_all.PlznitoApiError),
    ],
)
def test_download_one_id_failure_raises(mocker: MockerFixture, response: MagicMock, error: type[Exception]) -> None:
    mocker.patch.object(restore_all.SESSION, 'get', return_value=response)
    with pytest.raises(error):
        restore_all.download_one_id(1)


def test_download_one_id_network_error_raises(mocker: MockerFixture) -> None:
    mocker.patch.object(restore_all.SESSION, 'get', side_effect=requests.ConnectionError("down"))
    with pytest.raises(requests.ConnectionError):
        restore_all.download_one_id(1)


def test_download_one_id_invalid_json_raises(mocker: MockerFixture) -> None:
    response = _response()
    response.json.side_effect = json.JSONDecodeError("bad", "", 0)
    mocker.patch.object(restore_all.SESSION, 'get', return_value=response)
    with pytest.raises(ValueError):
        restore_all.download_one_id(1)


def test_download_recent(mocker: MockerFixture) -> None:
    items = [API_TICKET, {'id': 55395, 'name': "Díra", 'description': "Hluboká"}, "junk"]
    mock_get = mocker.patch.object(
        restore_all.SESSION, 'get', return_value=_response({'count': 3, 'timestamp': 1, 'items': items})
    )

    records = restore_all.download_recent(90)

    assert mock_get.call_args.args[0] == "https://www.plznito.cz/api/3.0/tickets/list"
    assert mock_get.call_args.kwargs['params'] == {'recentDays': 90}
    assert [record['id'] for record in records] == [55394, 55395]
    assert records[1]['report'] == "Hluboká"


@pytest.mark.parametrize('payload', [{'count': 0}, {'items': None}, [], "<html>"])
def test_download_recent_unexpected_payload(mocker: MockerFixture, payload: object) -> None:
    mocker.patch.object(restore_all.SESSION, 'get', return_value=_response(payload))
    with pytest.raises(restore_all.PlznitoApiError):
        restore_all.download_recent(90)


def test_download_recent_http_error_fails_loudly(mocker: MockerFixture) -> None:
    mocker.patch.object(restore_all.SESSION, 'get', return_value=_response(status=502))
    with pytest.raises(requests.HTTPError):
        restore_all.download_recent(90)


def test_session_does_not_retry_503() -> None:
    retry = restore_all.SESSION.get_adapter('https://www.plznito.cz').max_retries
    assert 503 not in retry.status_forcelist
    assert 502 in retry.status_forcelist


def test_download_range_skips_existing_and_failed(mocker: MockerFixture, tmp_path: pathlib.Path) -> None:
    (tmp_path / '1.json').write_text('{"item": {"id": 1}}', encoding='utf-8')
    downloads = {2: {'item': {'id': 2}}, 3: {}}
    mock_download = mocker.patch.object(restore_all, 'download_one_id', side_effect=lambda i: downloads[i])

    restore_all.download_range(str(tmp_path), 1, 3)

    assert [call.args[0] for call in mock_download.call_args_list] == [2, 3]
    assert sorted(p.name for p in tmp_path.iterdir()) == ['1.json', '2.json']
    assert json.loads((tmp_path / '2.json').read_text(encoding='utf-8')) == {'item': {'id': 2}}


def test_download_range_stops_on_failure_and_resumes(mocker: MockerFixture, tmp_path: pathlib.Path) -> None:
    def flaky(ticket_id: int) -> dict:
        if ticket_id == 2:
            raise requests.ConnectionError("down")
        return {'item': {'id': ticket_id}}

    mocker.patch.object(restore_all, 'download_one_id', side_effect=flaky)
    with pytest.raises(requests.ConnectionError):
        restore_all.download_range(str(tmp_path), 1, 3)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['1.json']

    mock_download = mocker.patch.object(restore_all, 'download_one_id', side_effect=lambda i: {'item': {'id': i}})
    restore_all.download_range(str(tmp_path), 1, 3)
    assert [call.args[0] for call in mock_download.call_args_list] == [2, 3]

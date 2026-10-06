# Copyright 2026 Pexeso Inc. All rights reserved.
from unittest.mock import MagicMock, patch

import pytest

from app import app
from app import routes
from app import train_delays


# Markup mirrors the real Babitron page: uppercase tags, unquoted attributes
# and <TD> cells that are never explicitly closed.
_PAGE_HTML = (
    "<html><body>"
    "<TABLE ALIGN=CENTER BGCOLOR=0000ff CELLPADDING=2>"
    "<TR BGCOLOR=ffffff><TH>vlak<TH>&nbsp;<TH>&nbsp;<TH>stanice<TH>prav./skut. čas<TH>zpoždění</TR>"
    "<TR><TD><A HREF=\"cgi-bin/zpvlaku.cgi?55\">rj 55</A><TD>Vindobona"
    "<TD>Praha hl.n. &ndash; Wien Hbf<TD>Svitava odbočka"
    "<TD>11:04/11:15 (odj.)<TD>11 min. zpoždění</TR>"
    "<TR><TD><A HREF=\"cgi-bin/zpvlaku.cgi?123\">Bus EC 123</A><TD>Valašský expres"
    "<TD>Praha hl.n. &ndash; Púchov<TD>Vsetín (NAD)"
    "<TD>11:07/11:07 (odj.)<TD>bez zpoždění</TR>"
    "<TR><TD><TD>prázdný vlak<TD><TD><TD><TD></TR>"
    "<TR><TD>Os 1<TD>málo buněk</TR>"
    "</TABLE>"
    "</body></html>"
)


def _mock_response(text: str, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    # Bytes decoded as latin-1 imitate requests' guess when no charset is sent.
    response.text = text.encode('utf-8').decode('latin-1')
    type(response).encoding = property(
        lambda self: None,
        lambda self, value: setattr(self, 'text', text),
    )
    return response


# ── parsing helpers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('delay_text, expected_status, expected_minutes', [
    ("bez zpoždění", 'on_time', 0),
    ("včas", 'on_time', 0),
    ("<b>12 min. zpoždění</b>", 'delayed', 12),
    ("+5", 'delayed', 5),
    ("vlak zrušen", 'canceled', None),
    ("odklon", 'diverted', None),
    ("výluka", 'disruption', None),
    ("", 'unknown', None),
    ("neznámý stav", 'unknown', None),
])
def test_parse_delay_status_and_minutes(delay_text: str, expected_status: str,
                                        expected_minutes: int | None) -> None:
    status, minutes = train_delays.parse_delay_status_and_minutes(delay_text)
    assert status == expected_status
    assert minutes == expected_minutes


@pytest.mark.parametrize('delay_text, expected', [
    ("", None),
    ("bez zpoždění", 0),
    ("včas", 0),
    ("7 min. zpoždění", 7),
    ("zrušen", None),
    ("odklon", None),
    ("výluka", None),
    ("1234 min.", None),
])
def test_get_delay(delay_text: str, expected: int | None) -> None:
    assert train_delays.get_delay(delay_text) == expected


@pytest.mark.parametrize('train_text, expected', [
    ("rj 55", ('rj', 55)),
    ("EC103", ('EC', 103)),
    ("Bus EC 123", ('EC', 123)),
    ("Sp 1408", ('Sp', 1408)),
    ("vlak", (None, None)),
    ("", (None, None)),
])
def test_parse_train_identity(train_text: str, expected: tuple) -> None:
    assert train_delays.parse_train_identity(train_text) == expected


@pytest.mark.parametrize('text, expected', [
    ("11:04/11:15 (odj.)", ('11:04', '11:15')),
    ("9:58 (příj.)", ('9:58', None)),
    ("bez času", (None, None)),
])
def test_parse_scheduled_actual_times(text: str, expected: tuple) -> None:
    assert train_delays.parse_scheduled_actual_times(text) == expected


@pytest.mark.parametrize('url, expected', [
    ('https://babitron.kam.mff.cuni.cz/zponline.html', 'zponline'),
    ('https://babitron.kam.mff.cuni.cz/zponlineos.html', 'zponlineos'),
    ('https://babitron.kam.mff.cuni.cz/ZPONLINEOS.HTML', 'zponlineos'),
    ('https://kam.mff.cuni.cz/~babilon/zponlineos', 'zponlineos'),
    ('https://kam.mff.cuni.cz/~babilon/zponlineos/', 'zponlineos'),
    ('https://kam.mff.cuni.cz/~babilon/zponline', 'zponline'),
])
def test_source_page_from_url(url: str, expected: str) -> None:
    assert train_delays.source_page_from_url(url) == expected


def test_normalize_text_strips_diacritics_and_lowercases() -> None:
    assert train_delays.normalize_text("Bez ZPOŽDĚNÍ, Výluka") == "bez zpozdeni, vyluka"


# ── scrape_babitron_delays ────────────────────────────────────────────────────

@patch('app.train_delays.requests.get')
def test_scrape_parses_rows_from_unclosed_cells(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_response(_PAGE_HTML)

    result = train_delays.scrape_babitron_delays('https://babitron.kam.mff.cuni.cz/zponlineos.html')

    # Empty train cell and short rows are skipped.
    assert list(result) == ['rj 55', 'Bus EC 123']

    rj = result['rj 55']
    assert rj['name'] == "Vindobona"
    assert rj['station'] == "Svitava odbočka"
    assert rj['route_text'] == "Praha hl.n. – Wien Hbf"
    assert rj['station_text'] == "Svitava odbočka"
    assert rj['status'] == 'delayed'
    assert rj['delay'] == 11
    assert rj['delay_minutes'] == 11
    assert rj['train_category'] == 'rj'
    assert rj['train_number'] == 55
    assert rj['scheduled_time_hhmm'] == '11:04'
    assert rj['actual_time_hhmm'] == '11:15'
    assert rj['source_page'] == 'zponlineos'

    bus = result['Bus EC 123']
    assert bus['status'] == 'on_time'
    assert bus['delay_minutes'] == 0
    assert bus['train_category'] == 'EC'
    assert bus['train_number'] == 123


@patch('app.train_delays.requests.get')
def test_scrape_returns_empty_dict_when_table_missing(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_response("<html><body><p>nic</p></body></html>")

    assert train_delays.scrape_babitron_delays('https://example.com/zponline.html') == {}


@patch('app.train_delays.requests.get')
def test_scrape_raises_on_http_error(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_response("", status_code=503)

    with pytest.raises(Exception, match='503'):
        train_delays.scrape_babitron_delays('https://example.com/zponline.html')


# ── /train_delays/ endpoint ───────────────────────────────────────────────────

@pytest.fixture
def client():
    routes.cache.clear()
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client
    routes.cache.clear()


@patch('app.routes.scrape_babitron_delays')
def test_endpoint_merges_both_sources(mock_scrape: MagicMock, client) -> None:
    mock_scrape.side_effect = [
        {'rj 55': {'source_page': 'zponline'}, 'Os 1': {'source_page': 'zponline'}},
        {'Os 1': {'source_page': 'zponlineos'}},
    ]

    response = client.get('/train_delays/')

    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == routes.CORS_ALLOW_ORIGIN
    # The OS page wins on duplicate keys.
    assert response.get_json() == {
        'rj 55': {'source_page': 'zponline'},
        'Os 1': {'source_page': 'zponlineos'},
    }
    called_urls = [call.args[0] for call in mock_scrape.call_args_list]
    assert called_urls == [routes.TRAIN_DELAYS_SOURCE_R_URL, routes.TRAIN_DELAYS_SOURCE_OS_URL]


@patch('app.routes.scrape_babitron_delays')
def test_endpoint_is_cached(mock_scrape: MagicMock, client) -> None:
    mock_scrape.return_value = {}

    client.get('/train_delays/')
    client.get('/train_delays/')

    assert mock_scrape.call_count == 2  # one call per source, only on the first request


@patch('app.routes.scrape_babitron_delays')
def test_endpoint_options_preflight(mock_scrape: MagicMock, client) -> None:
    response = client.options('/train_delays/')

    assert response.status_code in (200, 204)
    assert 'GET' in response.headers['Access-Control-Allow-Methods']

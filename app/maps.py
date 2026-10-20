# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Pre-rendered Plznito map pages, served gzipped and cacheable."""

import datetime
import gzip
import os

from flask import Blueprint, Response, current_app, render_template, request
from jinja2 import TemplateNotFound
from werkzeug.wrappers import Response as BaseResponse

bp = Blueprint('maps', __name__)

_RENDER_COMMAND = 'python -m plznito_monitoring.run_map_render'
# Browsers may reuse a page this long without asking; after that a cheap 304 until the next cron render.
CACHE_MAX_AGE_SECONDS = 300
# (page template, embed) -> (map file mtime, rendered HTML, gzipped HTML). One entry per page variant.
_page_cache: dict[tuple[str, bool], tuple[float, bytes, bytes]] = {}


def _cached_page(page_template: str, map_name: str, embed: bool) -> tuple[float, bytes, bytes]:
    """
    Rendered and gzipped page, re-rendered only when the generated map file changes.

    The maps are 1-16 MB; rendering and compressing them on every request wastes CPU, and
    sending them uncompressed makes slow clients drop the connection (uWSGI "write error").

    :param page_template: Page template that includes the generated map.
    :param map_name: File name of the generated map template.
    :param embed: Render without the page header.
    :raises TemplateNotFound: If the map has not been generated.
    :return: ``(map mtime, HTML, gzipped HTML)``.
    """
    map_file = current_app.jinja_env.get_template(map_name).filename
    mtime = os.stat(map_file).st_mtime if map_file else 0.0
    key = (page_template, embed)
    cached = _page_cache.get(key)
    if cached is None or cached[0] != mtime:
        html = render_template(page_template).encode('utf-8')
        cached = (mtime, html, gzip.compress(html, compresslevel=6))
        _page_cache[key] = cached
    return cached


def _render_map_page(page_template: str, map_name: str, render_args: str) -> BaseResponse | tuple[str, int]:
    """
    Serve a map page (gzipped when the client accepts it, 304 when unchanged), or a 503 page
    with the command to generate the missing map.

    :param page_template: Page template that includes the generated map.
    :param map_name: File name of the generated map template.
    :param render_args: Arguments for ``run_map_render`` that generate the map.
    :return: Page response, or ``(HTML, 503)``.
    """
    embed = bool(request.args.get('embed'))
    try:
        mtime, html, gzipped = _cached_page(page_template, map_name, embed)
    except TemplateNotFound:
        return render_template(
            'map_missing.html', map_name=map_name, generate_command=f'{_RENDER_COMMAND} {render_args}'
        ), 503

    use_gzip = 'gzip' in request.headers.get('Accept-Encoding', '').lower()
    response = Response(gzipped if use_gzip else html, mimetype='text/html')
    if use_gzip:
        response.headers['Content-Encoding'] = 'gzip'
    response.vary.add('Accept-Encoding')
    response.set_etag(f'{int(mtime)}-{int(embed)}-{"gz" if use_gzip else "id"}')
    response.last_modified = datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)
    response.cache_control.public = True
    response.cache_control.max_age = CACHE_MAX_AGE_SECONDS
    return response.make_conditional(request)


@bp.route('/plznito/map-bike')
def map_bike() -> BaseResponse | tuple[str, int]:
    """Plzeň cycling ticket map."""
    return _render_map_page(
        'plznito_index.html',
        'plznito_map.html',
        '--popup_mode api --file_in plznito_monitoring/plznito_cyklo.json'
        ' --file_out plznito_monitoring/templates/plznito_map.html',
    )


@bp.route('/plznito/map-all')
def map_all() -> BaseResponse | tuple[str, int]:
    """Plzeň all-tickets map."""
    return _render_map_page(
        'plznito_full_map_index.html',
        'plznito_map_all.html',
        '--cluster_style --popup_mode api --file_in plznito_monitoring/plznito_all.json'
        ' --file_out plznito_monitoring/templates/plznito_map_all.html',
    )

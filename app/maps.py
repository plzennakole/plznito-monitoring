# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Pre-rendered Plznito map pages."""

from flask import Blueprint, render_template
from jinja2 import TemplateNotFound

bp = Blueprint('maps', __name__)

_RENDER_COMMAND = 'python -m plznito_monitoring.run_map_render'


def _render_map_page(page_template: str, map_name: str, render_args: str) -> str | tuple[str, int]:
    """
    Render a map page, or a 503 page with the command to generate the missing map.

    :param page_template: Page template that includes the generated map.
    :param map_name: File name of the generated map template.
    :param render_args: Arguments for ``run_map_render`` that generate the map.
    :return: Page HTML, or ``(HTML, 503)``.
    """
    try:
        return render_template(page_template)
    except TemplateNotFound:
        return render_template(
            'map_missing.html', map_name=map_name, generate_command=f'{_RENDER_COMMAND} {render_args}'
        ), 503


@bp.route('/plznito/map-bike')
def map_bike() -> str | tuple[str, int]:
    """Plzeň cycling ticket map."""
    return _render_map_page(
        'plznito_index.html',
        'plznito_map.html',
        '--file_in plznito_monitoring/plznito_cyklo.json --file_out plznito_monitoring/templates/plznito_map.html',
    )


@bp.route('/plznito/map-all')
def map_all() -> str | tuple[str, int]:
    """Plzeň all-tickets map."""
    return _render_map_page(
        'plznito_full_map_index.html',
        'plznito_map_all.html',
        '--cluster_style --popup_mode full --file_in plznito_monitoring/plznito_all.json'
        ' --file_out plznito_monitoring/templates/plznito_map_all.html',
    )

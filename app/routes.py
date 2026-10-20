# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Endpoint index page."""

from flask import Response

from app import app


def _endpoint_index() -> list[tuple[str, str]]:
    """
    Public GET routes with the first docstring line of their view.

    :return: ``(path, description)`` pairs sorted by path.
    """
    endpoints = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static' or 'GET' not in (rule.methods or set()):
            continue
        doc = app.view_functions[rule.endpoint].__doc__ or ''
        endpoints.append((rule.rule, doc.strip().split('\n')[0]))
    return sorted(endpoints)


@app.route('/')
def root() -> Response:
    """Endpoint listing."""
    lines = ["<pre>"]
    lines += [f"GET     {path:45}  {description}" for path, description in _endpoint_index()]
    lines.append("</pre>")
    return Response("\n".join(lines), mimetype='text/html')


application = app

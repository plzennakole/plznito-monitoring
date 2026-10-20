# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Plznito issues REST API (flask-smorest blueprint, documented via OpenAPI)."""

import datetime
import pathlib
from collections import Counter
from typing import Any

from flask import request
from flask_smorest import Blueprint, abort
from marshmallow import Schema, fields, validate
from werkzeug.wrappers import Response

from app.cors import add_cors_headers
from app.plznito_store import PLZNITO_TZ, IssueNotFoundError, IssueStore, db_path_from_env, filter_issues

CACHE_MAX_AGE_SECONDS = 300

blp = Blueprint(
    'plznito_api',
    __name__,
    url_prefix='/plznito/api',
    description="Issues reported to Plznito (plznito.cz), refreshed every few hours.",
)

blp.after_request(add_cors_headers)

_stores: dict[pathlib.Path, IssueStore] = {}


def get_store() -> IssueStore:
    """
    Return the (process-wide) issue store for the configured JSON DB path.

    :return: Issue store.
    """
    path = db_path_from_env()
    if path not in _stores:
        _stores[path] = IssueStore(path)
    return _stores[path]


# ── Schemas ──────────────────────────────────────────────────────────────────


class CommonQuerySchema(Schema):
    status_id = fields.Integer(metadata={'description': "Status id (2 = V řešení, 3 = Vyřešeno, 6 = Odpovězeno)."})
    category_id = fields.Integer(metadata={'description': "Category id."})
    cyklo = fields.Boolean(metadata={'description': "Only cycling-related (true) or only other (false) issues."})
    limit = fields.Integer(load_default=100, validate=validate.Range(min=1, max=1000))
    offset = fields.Integer(load_default=0, validate=validate.Range(min=0))


class IssuesQuerySchema(CommonQuerySchema):
    since = fields.Date(metadata={'description': "Created on or after this date (YYYY-MM-DD, Prague time)."})
    until = fields.Date(metadata={'description': "Created on or before this date (YYYY-MM-DD, Prague time)."})
    since_id = fields.Integer(metadata={'description': "Only issues with id greater than this (incremental sync)."})


class NewIssuesQuerySchema(CommonQuerySchema):
    days = fields.Integer(
        load_default=7,
        validate=validate.Range(min=0, max=365),
        metadata={'description': "Issues created today or in the previous N days."},
    )


class IssueSchema(Schema):
    id = fields.Integer(required=True)
    name = fields.String()
    description = fields.String()
    solution = fields.String()
    status_id = fields.Integer()
    status_label = fields.String()
    category_id = fields.Integer(allow_none=True)
    category_label = fields.String()
    created = fields.DateTime(metadata={'description': "Creation time with Europe/Prague offset."})
    latitude = fields.Float()
    longitude = fields.Float()
    address = fields.String(allow_none=True)
    photo_url = fields.String(allow_none=True)
    is_cyklo = fields.Boolean()
    ticket_url = fields.String()


class IssueListSchema(Schema):
    total = fields.Integer(metadata={'description': "Number of matching issues before pagination."})
    limit = fields.Integer()
    offset = fields.Integer()
    updated_at = fields.DateTime(metadata={'description': "When the data was last downloaded."})
    items = fields.List(fields.Nested(IssueSchema))


class FacetSchema(Schema):
    id = fields.Integer(allow_none=True)
    label = fields.String()
    count = fields.Integer()


class MetaSchema(Schema):
    updated_at = fields.DateTime()
    total = fields.Integer()
    cyklo_total = fields.Integer()
    statuses = fields.List(fields.Nested(FacetSchema))
    categories = fields.List(fields.Nested(FacetSchema))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _paginate(issues: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
    """
    Build the list response for already filtered issues.

    :param issues: Filtered issues, newest first.
    :param limit: Page size.
    :param offset: Number of issues to skip.
    :return: List response payload.
    """
    return {
        'total': len(issues),
        'limit': limit,
        'offset': offset,
        'updated_at': get_store().updated_at,
        'items': issues[offset : offset + limit],
    }


def _today() -> datetime.date:
    """
    :return: Current date in Prague.
    """
    return datetime.datetime.now(PLZNITO_TZ).date()


def _facets(issues: list[dict[str, Any]], id_key: str, label_key: str) -> list[dict[str, Any]]:
    """
    Count issues per (id, label) pair, most frequent first.

    :param issues: API issues.
    :param id_key: Issue key holding the facet id.
    :param label_key: Issue key holding the facet label.
    :return: Facet entries with ``id``, ``label`` and ``count``.
    """
    counts = Counter((issue[id_key], issue[label_key]) for issue in issues)
    return [{'id': facet_id, 'label': label, 'count': count} for (facet_id, label), count in counts.most_common()]


@blp.after_request
def add_cache_headers(response: Response) -> Response:
    """
    Make successful responses cacheable until the next data download.

    :param response: Outgoing response.
    :return: Response with ``Last-Modified``/``Cache-Control``, or 304 if the client is up to date.
    """
    if response.status_code != 200:
        return response
    response.last_modified = get_store().updated_at
    response.cache_control.public = True
    response.cache_control.max_age = CACHE_MAX_AGE_SECONDS
    return response.make_conditional(request)


# ── Endpoints ────────────────────────────────────────────────────────────────


@blp.route('/issues')
@blp.arguments(IssuesQuerySchema, location='query', as_kwargs=True)
@blp.response(200, IssueListSchema)
def list_issues(limit: int, offset: int, **filters: Any) -> dict[str, Any]:
    """List issues, newest first.

    All filters are optional and combined with AND.
    """
    issues = filter_issues(get_store().get_issues(), **filters)
    return _paginate(issues, limit, offset)


@blp.route('/issues/new')
@blp.arguments(NewIssuesQuerySchema, location='query', as_kwargs=True)
@blp.response(200, IssueListSchema)
def list_new_issues(limit: int, offset: int, days: int, **filters: Any) -> dict[str, Any]:
    """List issues created in the last N days, newest first.

    ``days=0`` returns today's issues only.
    """
    since = _today() - datetime.timedelta(days=days)
    issues = filter_issues(get_store().get_issues(), since=since, **filters)
    return _paginate(issues, limit, offset)


@blp.route('/issues/<int:issue_id>')
@blp.response(200, IssueSchema)
@blp.alt_response(404, description="Issue not found")
def get_issue(issue_id: int) -> dict[str, Any]:
    """Get a single issue by its Plznito id."""
    try:
        issue = get_store().get_issue(issue_id)
    except IssueNotFoundError as error:
        abort(404, message=str(error))
    return issue


@blp.route('/meta')
@blp.response(200, MetaSchema)
def get_meta() -> dict[str, Any]:
    """Dataset info: last update, totals and available statuses/categories for filters."""
    store = get_store()
    issues = store.get_issues()
    return {
        'updated_at': store.updated_at,
        'total': len(issues),
        'cyklo_total': sum(issue['is_cyklo'] for issue in issues),
        'statuses': _facets(issues, 'status_id', 'status_label'),
        'categories': _facets(issues, 'category_id', 'category_label'),
    }

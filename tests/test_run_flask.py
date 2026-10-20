# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import json
import pathlib

import pytest

import run_flask


def test_load_config_merges_extra_files(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = tmp_path / 'config.json'
    default.write_text(json.dumps({'port': 5000, 'httpserver': 'flask'}), encoding='utf-8')
    extra = tmp_path / 'extra.json'
    extra.write_text(json.dumps({'port': 8000}), encoding='utf-8')
    monkeypatch.setattr(run_flask, 'DEFAULT_CONFIG_PATH', str(default))

    assert run_flask.load_config([str(extra)]) == {'port': 8000, 'httpserver': 'flask'}


def test_load_config_missing_extra_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_flask.load_config([str(tmp_path / 'missing.json')])


def test_repo_config_has_debugger_off() -> None:
    assert run_flask.load_config([])['flask']['debug'] is False

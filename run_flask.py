# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""
Run the web app with the server set in ``config.json`` (``flask``, ``tornado`` or ``gevent``).

    $ python run_flask.py [-c extra_config.json ...]

For production, ``gunicorn -w 2 -b 0.0.0.0:5000 app:app`` works without this script.
"""

import argparse
import json
import logging
import os
from typing import Any

from app import app

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')


def load_config(extra_paths: list[str]) -> dict[str, Any]:
    """
    Merge ``config.json`` (if present) with extra config files, later files win.

    :param extra_paths: Additional config files; each must exist.
    :raises FileNotFoundError: If an extra config file is missing.
    :return: Merged config.
    """
    for path in extra_paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file {path} not found")

    config: dict[str, Any] = {}
    for path in [DEFAULT_CONFIG_PATH, *extra_paths]:
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fr:
                config.update(json.load(fr))
    return config


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Run \"Plznito monitoring\" server.")
    parser.add_argument('--configs', '-c', nargs='*', default=[], help="additional config file(s)")
    args = parser.parse_args()

    config = load_config(args.configs)
    app.config['app'] = config
    app.config['root'] = os.path.dirname(os.path.abspath(__file__))

    server = config['httpserver']
    if server == 'flask':
        logging.getLogger(__name__).info("Starting Flask server with config %s", config)
        app.run(host=config['flask']['host'], port=config['port'],
                debug=config['flask']['debug'], threaded=True)
    elif server == 'tornado':
        from tornado.httpserver import HTTPServer
        from tornado.ioloop import IOLoop
        from tornado.log import enable_pretty_logging
        from tornado.wsgi import WSGIContainer

        enable_pretty_logging()
        http_server = HTTPServer(WSGIContainer(app))
        http_server.listen(port=config['port'])
        IOLoop.current().start()
    elif server == 'gevent':
        from gevent.pywsgi import WSGIServer

        WSGIServer(('', config['port']), app).serve_forever()
    else:
        raise ValueError(f"Wrong httpserver: {server}")


if __name__ == '__main__':
    main()

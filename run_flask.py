""" flask_example.py
    Required packages:
    - flask
    - folium
    Usage:
    Start the flask server by running:
        $ python flask_example.py
    And then head to http://127.0.0.1:5000/ in your browser to see the map displayed
"""

import os
import argparse
import json

from app import app

if __name__ == '__main__':
    if __name__ == '__main__':
        parser = argparse.ArgumentParser(description='Run "Plznito monitoring" server.')
        parser.add_argument('--configs', '-c', nargs='*', help='additional config file(s)')

        args = parser.parse_args()

        # load config files, store data in app.config
        app.config['app'] = {}

        config_default_fn = os.path.join(os.path.dirname(__file__), 'config.json')
        config_fns = [config_default_fn]

        if args.configs:
            for fn in args.configs:
                if not os.path.exists(fn):
                    raise Exception('Config file %s not found' % fn)
                config_fns.append(fn)

        for config_fn in config_fns:
            if os.path.exists(config_fn):
                with open(config_fn) as fr:
                    app.config['app'].update(json.load(fr))

        # additional config params
        app.config['root'] = os.path.join(os.path.dirname(os.path.abspath(__file__)))

        # run server
        if app.config['app']['httpserver'] == 'flask':
            print(app.config['app'])
            app.run(host=app.config['app']['flask']['host'], port=app.config['app']['port'],
                    debug=app.config['app']['flask']['debug'],
                    threaded=True)  # , processes=app.config['app']['flask']['processes']

        elif app.config['app']['httpserver'] == 'tornado':
            from tornado.wsgi import WSGIContainer
            from tornado.httpserver import HTTPServer
            from tornado.ioloop import IOLoop
            from tornado.log import enable_pretty_logging

            enable_pretty_logging()
            http_server = HTTPServer(WSGIContainer(app))
            http_server.listen(port=app.config['app']['port'])
            IOLoop.instance().start()
        elif app.config['app']['httpserver'] == 'gevent':
            from gevent.pywsgi import WSGIServer

            http_server = WSGIServer(('', app.config['app']['port']), app)
            http_server.serve_forever()
        else:
            raise Exception('Wrong httpserver: %s' % app.config['app']['httpserver'])

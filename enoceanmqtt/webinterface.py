# Author: Marc Alexandre K. <marcalexandrek-developer@yahoo.fr>
"""embedded web server providing the sensor management interface

A deliberately minimal, dependency-free HTTP server (stdlib ``http.server``) that
exposes:

* ``GET  /``            - the single-page management UI
* ``GET  /api/status``  - current runtime status: sensors, EEP catalog, gateway
* ``POST /api/learn``   - enable/disable UTE teach-in mode
* ``POST /api/sensors`` - add a sensor (manual or via EEP selection)
* ``POST /api/teachin``  - send a teach-in telegram to an actor
* ``DELETE /api/sensors/<name>`` - remove a sensor

The interface is intended to be put behind the Cosmos Proxy (an authentication
proxy), therefore it performs no authentication of its own.
"""
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


# -----------------------------------------------------------------------------
# static assets (kept as python strings so the package stays self-contained)
# -----------------------------------------------------------------------------
def _asset(name):
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, 'webui', name)
    try:
        with open(path, 'r', encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


INDEX_HTML = _asset('index.html')
STYLE_CSS = _asset('style.css')
APP_JS = _asset('app.js')


class WebInterface:
    """ties the HTTP server to the communicator backend"""

    def __init__(self, communicator):
        self.communicator = communicator
        self._srv = None
        self._thread = None

    def start(self, host='0.0.0.0', port=8091):
        """start the HTTP server in a background thread (non-blocking)"""
        handler = self._handler_class()
        try:
            self._srv = ThreadingHTTPServer((host, port), handler)
        except OSError as exc:
            logging.error("Cannot start web interface on %s:%d: %s", host, port, exc)
            return False
        self._thread = threading.Thread(target=self._srv.serve_forever,
                                        daemon=True, name='webui')
        self._thread.start()
        logging.info("Web interface listening on http://%s:%d", host, port)
        return True

    def stop(self):
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    # -- controller methods (bound into the handler) --------------------------
    def get_status(self):
        com = self.communicator
        sensors = []
        for sensor in com.sensors:
            sensors.append(com.describe_sensor(sensor))
        return {
            'gateway': {
                'connected': com.enocean is not None and com.enocean.is_alive(),
                'base_id': com.enocean_sender_hex,
                'mqtt': com.mqtt.is_connected() if com.mqtt else False,
            },
            'learn_mode': com.learn_mode,
            'sensors': sensors,
            'eep': com.eep_catalog(),
        }

    def enable_learn(self, enabled):
        self.communicator.set_learn_mode(bool(enabled))

    def add_sensor(self, payload):
        return self.communicator.add_sensor(payload)

    def send_teachin(self, name):
        """send a teach-in telegram to an actor"""
        ok, message = self.communicator._send_teachin(name)
        return {'ok': ok, 'message': message}

    def remove_sensor(self, name):
        return self.communicator.remove_sensor(name)

    # -- handler factory ------------------------------------------------------
    def _handler_class(self):
        web = self

        class Handler(BaseHTTPRequestHandler):
            # silence default logging
            def log_message(self, fmt, *args):   # pylint: disable=arguments-differ
                logging.debug("webui: " + fmt, *args)

            def _send(self, code, body, content_type='application/json; charset=utf-8'):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode('utf-8')
                elif isinstance(body, str):
                    body = body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self):
                length = int(self.headers.get('Content-Length') or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    return {}

            # -- routing ------------------------------------------------------
            def _route(self):
                parsed = urlparse(self.path)
                path = parsed.path.rstrip('/') or '/'

                if self.command == 'GET' and path == '/':
                    if INDEX_HTML is None:
                        return self._send(500, {'ok': False, 'error': 'index.html missing'})
                    return self._send(200, INDEX_HTML, 'text/html; charset=utf-8')
                if self.command == 'GET' and path == '/style.css':
                    if STYLE_CSS is None:
                        return self._send(404, {'ok': False, 'error': 'not found'})
                    return self._send(200, STYLE_CSS, 'text/css; charset=utf-8')
                if self.command == 'GET' and path == '/app.js':
                    if APP_JS is None:
                        return self._send(404, {'ok': False, 'error': 'not found'})
                    return self._send(200, APP_JS, 'application/javascript; charset=utf-8')

                # --- JSON API ---
                if self.command == 'GET' and path == '/api/status':
                    return self._send(200, web.get_status())
                if self.command == 'POST' and path == '/api/learn':
                    body = self._read_body()
                    web.enable_learn(bool(body.get('enabled')))
                    return self._send(200, {'ok': True, 'learn_mode': web.communicator.learn_mode})
                if self.command == 'POST' and path == '/api/sensors':
                    result = web.add_sensor(self._read_body())
                    code = 200 if result.get('ok') else 400
                    return self._send(code, result)
                if self.command == 'POST' and path == '/api/teachin':
                    result = web.send_teachin(str(self._read_body().get('name', '')))
                    code = 200 if result.get('ok') else 400
                    return self._send(code, result)
                if self.command == 'DELETE' and path.startswith('/api/sensors/'):
                    name = path[len('/api/sensors/'):]
                    result = web.remove_sensor(name)
                    code = 200 if result.get('ok') else 404
                    return self._send(code, result)

                return self._send(404, {'ok': False, 'error': 'not found'})

            def do_GET(self):   # pylint: disable=invalid-name
                self._route()

            def do_POST(self):   # pylint: disable=invalid-name
                self._route()

            def do_DELETE(self):   # pylint: disable=invalid-name
                self._route()

        return Handler

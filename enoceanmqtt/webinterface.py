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
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from enoceanmqtt.communicator import parse_eep, parse_int


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


def _binary_asset(name):
    """load a binary file from the webui directory, or None"""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, 'webui', name)
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        return None


INDEX_HTML = _asset('index.html')
STYLE_CSS = _asset('style.css')
APP_JS = _asset('app.js')
FAVICON_PNG = _binary_asset('icon.png')


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
    def discover(self):
        """return locally attached EnOcean dongles + mDNS ser2net endpoints.

        Serves the background discovery cache so the handler never blocks
        for the multi-second mDNS sweep; the cache is refreshed every ~10s
        by the communicator's gw-discovery thread."""
        com = self.communicator
        cache = getattr(com, 'get_discovery_cache', None)
        if cache is not None:
            return cache()
        # fallback (very old/test communicators): run a synchronous sweep
        from enoceanmqtt.device_discovery import discover
        return discover()

    def get_status(self):
        com = self.communicator
        sensors = []
        for sensor in com.sensors:
            sensors.append(com.describe_sensor(sensor))
        connected = com.gateway_connected()
        # 'discovering' means the gateway is genuinely hunting for a first
        # transceiver: no port has been configured yet. When a port IS set
        # but the transceiver is down (dongle unplugged, ser2net host
        # unreachable) we must NOT claim it is still searching - the web UI
        # shows 'disconnected' instead of a misleading 'searching...'.
        has_port = bool(str(com.conf.get('enocean_port') or '').strip())
        return {
            'gateway': {
                'connected': connected,
                'discovering': (not connected and not has_port
                                and getattr(com, 'enocean_sender', None) is None),
                'base_id': com.enocean_sender_hex,
                'error': getattr(com, 'enocean_error', None),
                'mqtt': (com.mqtt.is_connected() if com.mqtt else False),
                'diagnostics': com.diagnostics,
            },
            'learn_mode': com.learn_mode,
            'sensors': sensors,
            'eep': com.eep_catalog(),
            'virtual_senders': com.virtual_senders(),
            'next_free_sender': com.next_free_sender(),
        }

    def get_config(self):
        """return the current [CONFIG] settings for the web configurator"""
        return self.communicator.conf

    def get_history(self, name):
        return self.communicator.get_history(name)

    def save_config(self, payload):
        """persist updated [CONFIG] settings back to the configuration file"""
        return self.communicator.save_config(payload)

    def restart_gateway(self):
        """schedule a restart of the gateway process (via the run loop)"""
        return self.communicator.request_restart()

    def enable_learn(self, enabled):
        self.communicator.set_learn_mode(bool(enabled))

    def add_sensor(self, payload):
        return self.communicator.add_sensor(payload)

    def send_teachin(self, name, payload=None):
        """send a teach-in telegram to an actor.

        Existing device: pass only ``name``.
        Not-yet-saved device (add-actor popup): pass ``sender`` + ``eep`` (+
        optional ``address`` / ``category``) so the telegram can be sent
        without first creating a sensor.
        """
        payload = payload or {}
        if name:
            ok, message = self.communicator._send_teachin(name)
            return {'ok': ok, 'message': message}
        sender = payload.get('sender')
        eep_parts = parse_eep(str(payload.get('eep', '')).strip())
        if eep_parts is None:
            return {'ok': False, 'message': 'Invalid EEP, expected e.g. A5-02-05'}
        rorg, func, type_ = eep_parts
        sender_int = parse_int(sender)
        if sender_int is None:
            return {'ok': False, 'message': 'Invalid sender address'}
        address = parse_int(payload.get('address'))
        ok, message = self.communicator._send_teachin_payload(
            name=payload.get('name') or '',
            sender_hex=sender_int, rorg=rorg, func=func, type_=type_,
            address=address,
            category=payload.get('category'), bidirectional=payload.get('bidirectional'))
        return {'ok': ok, 'message': message}

    def start_capture(self):
        self.communicator.start_capture()
        return {'ok': True, 'message': 'Teach-in capture started'}

    def stop_capture(self):
        self.communicator.stop_capture()
        return {'ok': True}

    def get_captured(self):
        dev = self.communicator.get_captured()
        return {'ok': dev is not None, 'device': dev}

    def remove_sensor(self, name):
        return self.communicator.remove_sensor(name)

    def update_sensor(self, name, payload):
        return self.communicator.update_sensor(name, payload)

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
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # the client (browser poller) went away mid-write - nothing
                    # we can do; swallowing it prevents the per-request
                    # traceback spam + thread death storm in the log (observed
                    # under heavy load).
                    pass
                except socket.timeout:
                    pass

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
                if self.command == 'GET' and path.startswith('/lang/'):
                    lang_name = path[len('/lang/'):]
                    text = _asset('lang/' + lang_name)
                    if text is None:
                        return self._send(404, {'ok': False, 'error': 'not found'})
                    return self._send(200, text, 'application/javascript; charset=utf-8')
                if self.command == 'GET' and path in ('/favicon.png', '/icon.png'):
                    if FAVICON_PNG is None:
                        return self._send(404, {'ok': False, 'error': 'not found'})
                    return self._send(200, FAVICON_PNG, 'image/png')

                # --- JSON API ---
                if self.command == 'GET' and path == '/api/status':
                    return self._send(200, web.get_status())
                if self.command == 'GET' and path == '/api/discovery':
                    return self._send(200, web.discover())
                if self.command == 'GET' and path == '/api/config':
                    return self._send(200, web.get_config())
                if self.command == 'POST' and path == '/api/restart':
                    result = web.restart_gateway()
                    return self._send(200, result)
                if self.command == 'POST' and path == '/api/config':
                    result = web.save_config(self._read_body())
                    code = 200 if result.get('ok') else 400
                    return self._send(code, result)
                if self.command == 'GET' and path.startswith('/api/history/'):
                    name = unquote(path[len('/api/history/'):])
                    result = web.get_history(name)
                    code = 200 if result.get('ok') else 404
                    return self._send(code, result)
                if self.command == 'POST' and path == '/api/learn':
                    body = self._read_body()
                    web.enable_learn(bool(body.get('enabled')))
                    return self._send(200, {'ok': True, 'learn_mode': web.communicator.learn_mode})
                if self.command == 'POST' and path == '/api/sensors':
                    result = web.add_sensor(self._read_body())
                    code = 200 if result.get('ok') else 400
                    return self._send(code, result)
                if self.command == 'POST' and path == '/api/teachin':
                    body = self._read_body()
                    result = web.send_teachin(str(body.get('name', '')), payload=body)
                    code = 200 if result.get('ok') else 400
                    return self._send(code, result)
                if self.command == 'POST' and path == '/api/teachin/capture':
                    result = web.start_capture()
                    return self._send(200, result)
                if self.command == 'POST' and path == '/api/teachin/capture/stop':
                    result = web.stop_capture()
                    return self._send(200, result)
                if self.command == 'GET' and path == '/api/teachin/captured':
                    return self._send(200, web.get_captured())
                if self.command == 'DELETE' and path.startswith('/api/sensors/'):
                    name = unquote(path[len('/api/sensors/'):])
                    result = web.remove_sensor(name)
                    code = 200 if result.get('ok') else 404
                    return self._send(code, result)
                if self.command in ('PUT', 'POST') and path.startswith('/api/sensors/'):
                    name = unquote(path[len('/api/sensors/'):])
                    result = web.update_sensor(name, self._read_body())
                    code = 200 if result.get('ok') else 400
                    return self._send(code, result)

                return self._send(404, {'ok': False, 'error': 'not found'})

            def do_GET(self):   # pylint: disable=invalid-name
                self._route()

            def do_POST(self):   # pylint: disable=invalid-name
                self._route()

            def do_DELETE(self):   # pylint: disable=invalid-name
                self._route()

            def do_PUT(self):   # pylint: disable=invalid-name
                self._route()

        return Handler

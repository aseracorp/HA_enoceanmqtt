"""Tests for the web interface, sensor store, EEP registry and UTE teach-in.

These tests use fakes (no real EnOcean gateway or MQTT broker needed).

Run with:  python -m pytest tests/  (or)  python tests/test_webui.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enocean.protocol.packet import UTETeachInPacket
from enocean.protocol.constants import PACKET, RORG

from enoceanmqtt.sensor_store import SensorStore
from enoceanmqtt.eep_registry import get_registry
from enoceanmqtt.communicator import Communicator
from enoceanmqtt.webinterface import WebInterface


def make_ute_packet():
    """build a bidirectional UTE teach-in telegram advertising EEP A5-02-05"""
    flags = 0x80
    data = [RORG.UTE, flags, 0x00, 0x01, 0x02, 0x05, 0x02, 0xA5,
            0xDE, 0xAD, 0xBE, 0xEF, 0x00]
    optional = [0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]
    packet = UTETeachInPacket(PACKET.RADIO_ERP1, data=data, optional=optional)
    packet.parse()
    return packet


class FakeEnocean:
    def __init__(self):
        self.sent = []
        self.base_id = [0xFF, 0x80, 0x00, 0x00]
        self.teach_in = False
        self.alive = True

    def is_alive(self):
        return self.alive

    def start(self):
        pass

    def stop(self):
        self.alive = False

    def send(self, packet):
        self.sent.append(packet)


class FakeMQTT:
    def __init__(self):
        self.published = []
        self.subs = []

    def is_connected(self):
        return True

    def subscribe(self, topic):
        self.subs.append(topic)

    def publish(self, topic, payload=None, retain=False, **kwargs):
        self.published.append((topic, payload, retain))

    def connect_async(self, *a, **k):
        pass

    def loop_start(self):
        pass


def test_sensor_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'sensors.json')
        store = SensorStore(path)
        store.add({'name': 'living_temp', 'address': 0x12345678,
                   'rorg': 0xA5, 'func': 0x02, 'type': 0x05})
        store.add({'name': 'hall_switch', 'address': 0x11111111,
                   'rorg': 0xF6, 'func': 0x02, 'type': 0x02})
        assert [s['name'] for s in store.all()] == ['living_temp', 'hall_switch']
        assert store.get('living_temp')['address'] == 0x12345678
        assert store.remove('hall_switch') is True
        assert store.remove('nope') is False

        # persistence across instances
        store2 = SensorStore(path)
        assert [s['name'] for s in store2.all()] == ['living_temp']


def test_eep_registry():
    reg = get_registry()
    assert len(reg.profiles) >= 90, 'EEP catalog should be populated'
    assert reg.get(0xA5, 0x02, 0x05) is not None
    assert reg.get(0xDE, 0xAD, 0xBE) is None
    assert any('temperature' in p['name'].lower() for p in reg.search('temperature'))


def test_ute_teachin():
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost',
            'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.enocean = FakeEnocean()
        com.mqtt = FakeMQTT()

        packet = make_ute_packet()

        # 1. teach-in disabled -> ignored
        com._handle_ute_packet(packet)
        assert com._store.all() == []

        # 2. enable, handle -> stored + merged + ack sent + one-shot
        com.set_learn_mode(True)
        com._handle_ute_packet(packet)
        stored = com._store.all()
        assert len(stored) == 1
        assert stored[0]['address'] == 0xDEADBEEF
        assert stored[0]['rorg'] == 0xA5 and stored[0]['func'] == 0x02
        assert stored[0]['type'] == 0x05
        assert com.learn_mode is False
        assert any(s.get('name') == 'enoceanmqtt/ute_deadbeef' for s in com.sensors)
        assert len(com.enocean.sent) == 1, 'UTE ack should be sent'

        # 3. manual add / duplicate / invalid inputs
        assert com.add_sensor({'name': 'x', 'address': '0x123', 'eep': 'A5-02-05'})['ok']
        assert not com.add_sensor({'name': 'x', 'address': '0x123', 'eep': 'A5-02-05'})['ok']
        assert not com.add_sensor({'name': 'y', 'address': '0x123', 'eep': 'bogus'})['ok']
        assert not com.add_sensor({'name': 'z', 'address': 'zzz', 'eep': 'A5-02-05'})['ok']

        # 4. remove
        assert com.remove_sensor('x')['ok']
        assert com._store.get('x') is None
        assert not any(s.get('name') == 'enoceanmqtt/x' for s in com.sensors)


def test_web_interface():
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost',
            'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.enocean = FakeEnocean()
        com.mqtt = FakeMQTT()

        web = WebInterface(com)
        # bind to an ephemeral port by using port 0 and reading the actual address
        web._srv = None
        import socket
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        # restart the server bound to that port
        web.stop()
        web.start(host='127.0.0.1', port=port)
        time.sleep(0.3)

        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/') as r:
                body = r.read()
                assert r.status == 200 and b'EnOceanMQTT' in body
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/style.css') as r:
                assert b':root' in r.read()
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status') as r:
                data = json.loads(r.read())
                assert 'gateway' in data and 'eep' in data and 'sensors' in data
        finally:
            web.stop()


if __name__ == '__main__':
    test_sensor_store()
    test_eep_registry()
    test_ute_teachin()
    test_web_interface()
    print('ALL TESTS PASSED')

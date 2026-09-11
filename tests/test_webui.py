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
                assert r.status == 200 and b'HA_enoceanmqtt' in body
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/style.css') as r:
                assert b':root' in r.read()
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status') as r:
                data = json.loads(r.read())
                assert 'gateway' in data and 'eep' in data and 'sensors' in data
        finally:
            web.stop()


def test_eep_classification():
    """profiles are classified as sensor/actor and marked bidir/smartack"""
    reg = get_registry()

    def p(eep):
        r, f, t = [int(x, 16) for x in eep.split('-')]
        return reg.get(r, f, t)

    assert p('A5-02-05')['category'] == 'sensor'
    assert p('A5-02-05')['bidirectional'] is False

    # A5-20-01: bi-directional actor
    assert p('A5-20-01')['category'] == 'actor'
    assert p('A5-20-01')['bidirectional'] is True

    # D2-11-01: smartACK sensor (added via override, not in old EEP.xml)
    assert p('D2-11-01')['category'] == 'sensor'
    assert p('D2-11-01')['smartack'] is True

    # D2-01-01: actor (electronic switch) that is also bidirectional
    assert p('D2-01-01')['category'] == 'actor'
    assert p('D2-01-01')['bidirectional'] is True


def test_bidirectional_and_smartack_reply():
    """bidirectional devices get replied to; smartACK is sent fast (before publish)"""
    import datetime
    from enoceanmqtt.communicator import Communicator

    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.enocean = FakeEnocean()
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # build a smartACK sensor (D2-11-01) and a normal sensor
        com.sensors = [
            {'name': 'enoceanmqtt/smart_device', 'address': 0xDEADBEEF,
             'rorg': 0xD2, 'func': 0x11, 'type': 0x01, 'smartack': True},
            {'name': 'enoceanmqtt/temp', 'address': 0x12345678,
             'rorg': 0xA5, 'func': 0x02, 'type': 0x05, 'smartack': False,
             'bidirectional': False},
            {'name': 'enoceanmqtt/actor', 'address': 0x11111111,
             'rorg': 0xA5, 'func': 0x14, 'type': 0x01, 'bidirectional': True,
             'category': 'actor'},
        ]
        com._index_sensors()

        # --- smartACK fast path: reply sent ---
        from enocean.protocol.packet import RadioPacket
        from enocean.protocol.constants import PACKET
        # craft a VLD packet from the smart device (raw - the old enocean
        # library does not know the D2-11-01 profile)
        vld = RadioPacket(PACKET.RADIO_ERP1,
                          data=[0xD2, 0x01, 0x00, 0x00, 0xDE, 0xAD, 0xBE, 0xEF, 0x00],
                          optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
        vld.parse()
        com._send_bidirectional_reply(vld, com.sensors[0])
        assert len(com.enocean.sent) >= 1, 'smartACK reply should be sent'
        sent = com.enocean.sent[-1]
        assert sent.rorg == 0xD2, 'smartACK reply should be VLD'
        assert sent.destination == [0xDE, 0xAD, 0xBE, 0xEF], \
            'reply should target the device sender'

        # --- bidirectional actor (A5-20-01) gets a reply too ---
        bs4 = RadioPacket(PACKET.RADIO_ERP1,
                          data=[0xA5, 0x00, 0x00, 0x00, 0x00, 0x11, 0x11, 0x11, 0x11, 0x00],
                          optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
        bs4.parse()
        com._send_bidirectional_reply(bs4, com.sensors[2])
        assert len(com.enocean.sent) >= 2, 'bidirectional actor reply should be sent'
        assert com.enocean.sent[-1].destination == [0x11, 0x11, 0x11, 0x11]

        # --- normal sensor does NOT get a reply ---
        n = len(com.enocean.sent)
        bs4b = RadioPacket(PACKET.RADIO_ERP1,
                           data=[0xA5, 0x00, 0x00, 0x00, 0x00, 0x12, 0x34, 0x56, 0x78, 0x00],
                           optional=[0x03, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
        bs4b.parse()
        assert not com._needs_bidirectional_reply(bs4b, com.sensors[1])
        com._send_bidirectional_reply(bs4b, com.sensors[1])
        assert len(com.enocean.sent) == n, 'plain sensor should not trigger a reply'

def test_teachin_captures_non_ute_devices():
    """learn mode captures unknown devices sending regular telegrams
    (4BS data, RPS/F6 switches without a teach-in button) - not just UTE"""
    import datetime
    from enocean.protocol.packet import RadioPacket

    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.enocean = FakeEnocean()
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # --- 1. 4BS regular data telegram (like the user's log) ---
        p = RadioPacket(PACKET.RADIO_ERP1,
                        data=[0xa5, 0xa0, 0x2e, 0xea, 0x0d, 0x05, 0xa2, 0xa1, 0x38, 0x00],
                        optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p.parse()
        p.received = datetime.datetime.utcnow()
        com.set_learn_mode(True)
        com._process_radio_packet(p)
        stored = com._store.all()
        assert len(stored) == 1, 'unknown 4BS device should be captured in learn mode'
        assert stored[0]['address'] == 0x05A2A138
        assert stored[0]['rorg'] == 0xA5
        assert com.learn_mode is False, 'teach-in should be one-shot'

        # --- 2. 4BS learn telegram (LRN bit) extracts EEP ---
        p2 = RadioPacket(PACKET.RADIO_ERP1,
                         data=[0xa5, 0x08, 0x00, 0x28, 0x02, 0x11, 0x22, 0x33, 0x44, 0x00],
                         optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p2.parse()
        p2.received = datetime.datetime.utcnow()
        assert com._is_4bs_learn_telegram(p2) is True
        com.set_learn_mode(True)
        com._process_radio_packet(p2)
        stored2 = [s for s in com._store.all() if s['address'] == 0x11223344]
        assert len(stored2) == 1
        assert stored2[0]['rorg'] == 0xA5 and stored2[0]['func'] == 0x02 and stored2[0]['type'] == 0x05

        # --- 3. RPS F6 switch (no teach-in button) ---
        p3 = RadioPacket(PACKET.RADIO_ERP1,
                         data=[0xf6, 0x10, 0x00, 0x55, 0x66, 0x77, 0x88, 0x00],
                         optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p3.parse()
        p3.received = datetime.datetime.utcnow()
        com.set_learn_mode(True)
        com._process_radio_packet(p3)
        stored3 = [s for s in com._store.all() if s['address'] == 0x55667788]
        assert len(stored3) == 1
        assert stored3[0]['rorg'] == 0xF6
        assert stored3[0]['func'] == 0x01, 'F6 should get a default EEP (push button)'


def test_send_teachin_to_actor():
    """sending a teach-in telegram to an actor works; plain sensors are rejected"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.enocean = FakeEnocean()
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # 4BS actor (A5-20-01)
        assert com.add_sensor({'name': 'my_actor', 'address': 0x0A0B0C0D, 'eep': 'A5-20-01'})['ok']
        ok, msg = com._send_teachin('my_actor')
        assert ok, msg
        assert len(com.enocean.sent) >= 1
        last = com.enocean.sent[-1]
        assert last.rorg == 0xA5
        assert getattr(last, 'learn', False) is not False

        # plain one-way sensor -> rejected
        assert com.add_sensor({'name': 'a_temp', 'address': 0x11111111, 'eep': 'A5-02-05'})['ok']
        ok2, _ = com._send_teachin('a_temp')
        assert ok2 is False

        # unknown device -> rejected
        ok3, _ = com._send_teachin('nope')
        assert ok3 is False

if __name__ == '__main__':
    test_sensor_store()
    test_eep_registry()
    test_ute_teachin()
    test_web_interface()
    test_eep_classification()
    test_bidirectional_and_smartack_reply()
    test_teachin_captures_non_ute_devices()
    test_send_teachin_to_actor()
    print('ALL TESTS PASSED')


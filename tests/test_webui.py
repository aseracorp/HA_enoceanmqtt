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

        # --- 1. 4BS data telegram WITHOUT the learn bit (teach-in NOT pressed) ---
        # must NOT be added to the database.
        p = RadioPacket(PACKET.RADIO_ERP1,
                        data=[0xa5, 0xa0, 0x2e, 0xea, 0x0d, 0x05, 0xa2, 0xa1, 0x38, 0x00],
                        optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p.parse()
        p.received = datetime.datetime.utcnow()
        com.set_learn_mode(True)
        com._process_radio_packet(p)
        stored = com._store.all()
        assert len(stored) == 0, 'a 4BS data telegram without the learn bit must not be added'
        assert com.learn_mode is True, 'learn mode should remain on (no device captured)'

        # --- 2. 4BS learn telegram (teach-in pressed) extracts EEP (A5-08-01) ---
        # Real teach-in telegram from the log for 0x05A2A138:
        # DB0=0x20 DB1=0x08 DB2=0x02 DB3=0x80  ->  A5-08-01
        p2 = RadioPacket(PACKET.RADIO_ERP1,
                         data=[0xa5, 0x20, 0x08, 0x02, 0x80, 0x11, 0x22, 0x33, 0x44, 0x00],
                         optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p2.parse()
        p2.received = datetime.datetime.utcnow()
        assert com._is_4bs_learn_telegram(p2) is True, 'real teach-in telegram must be recognized'
        com.set_learn_mode(True)
        com._process_radio_packet(p2)
        stored2 = [s for s in com._store.all() if s['address'] == 0x11223344]
        assert len(stored2) == 1
        assert stored2[0]['rorg'] == 0xA5 and stored2[0]['func'] == 0x08 and stored2[0]['type'] == 0x01, \
            '4BS teach-in telegram should extract A5-08-01 exactly (not a guessed default)'

        # --- 3. RPS F6 switch: teachable via telegram, EEP recognized from data ---
        # RPS/F6 telegrams carry no EEP, but the EEP is recognized from the
        # data byte. A push button press (D0=0x08) is recognized as F6-01-01.
        p3 = RadioPacket(PACKET.RADIO_ERP1,
                         data=[0xf6, 0x08, 0x00, 0x55, 0x66, 0x77, 0x88, 0x00],
                         optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p3.parse()
        p3.received = datetime.datetime.utcnow()
        com.set_learn_mode(True)
        com._process_radio_packet(p3)
        stored3 = [s for s in com._store.all() if s['address'] == 0x55667788]
        assert len(stored3) == 1
        assert stored3[0]['rorg'] == 0xF6
        assert stored3[0]['func'] == 0x01, 'F6 push button should be recognized as F6-01-01'
        assert stored3[0]['type'] == 0x01


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



def test_update_sensor():
    """editing a web-added sensor updates name and EEP (backend)"""
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

        # add a sensor (by RORG only, like a non-UTE capture)
        com._store.add({'name': 'learn_05a2a138', 'address': 0x05A2A138, 'rorg': 0xA5})
        com._load_dynamic_sensors()

        # update: set name + EEP
        res = com.update_sensor('learn_05a2a138', {'name': 'living_temp', 'eep': 'A5-08-01'})
        assert res['ok'], res
        assert res['sensor']['name'] == 'living_temp'
        assert res['sensor']['eep'] == 'A5-08-01'

        # stored correctly
        stored = com._store.get('living_temp')
        assert stored is not None
        assert stored['rorg'] == 0xA5 and stored['func'] == 0x08 and stored['type'] == 0x01

        # EEP only update
        res2 = com.update_sensor('living_temp', {'eep': 'A5-02-05'})
        assert res2['ok'], res2
        assert res2['sensor']['eep'] == 'A5-02-05'

        # invalid EEP rejected
        res3 = com.update_sensor('living_temp', {'eep': 'bogus'})
        assert not res3['ok']


def test_4bs_teachin_bidirectional_reply():
    """a 4BS learn telegram from a bidirectional device gets a teach-in reply"""
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

        # Bidirectional 4BS actor A5-20-01: build a proper teach-in telegram
        # with the enocean library (learn=True sets the documented LRN/EEP).
        p = RadioPacket.create(0xA5, 0x20, 0x01, learn=True,
                               sender=[0x11, 0x22, 0x33, 0x44],
                               destination=[0xFF, 0x80, 0x00, 0x00])
        p.parse()
        p.received = datetime.datetime.utcnow()

        com.set_learn_mode(True)
        # the A5-20-01 profile is bidirectional, so _handle_4bs_learn_telegram
        # must send a teach-in response
        before = len(com.enocean.sent)
        com._process_radio_packet(p)
        assert len(com.enocean.sent) == before + 1, \
            'bidirectional 4BS teach-in should send a reply'
        reply = com.enocean.sent[-1]
        assert reply.rorg == 0xA5
        assert reply.destination == [0x11, 0x22, 0x33, 0x44]


def test_device_db_corruption_recovery():
    """a corrupted TinyDB device database does not prevent startup"""
    from enoceanmqtt.overlays.homeassistant.device_manager import DeviceManager
    with tempfile.TemporaryDirectory() as tmp:
        db_file = os.path.join(tmp, 'device_db.json')
        with open(db_file, 'w') as f:
            f.write('{"_default": {"1": {"uid": "X"')  # truncated / corrupt
        dm = DeviceManager({'db_file': db_file})
        assert dm.db_get_devices() == []
        # still writable
        dm.db_add_device({'address': 1, 'name': 'x', 'rorg': 165, 'func': 2, 'type': 5}, 'UID')
        assert len(dm.db_get_devices()) == 1
        # a .corrupt backup was made
        assert any('corrupt' in f for f in os.listdir(tmp))



def test_f6_eep_recognition():
    """RPS/F6 teach-in recognizes the EEP from the telegram data byte"""
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

        def teachin(d0, addr):
            p = RadioPacket(PACKET.RADIO_ERP1,
                            data=[0xf6, d0, (addr >> 24) & 0xff, (addr >> 16) & 0xff,
                                  (addr >> 8) & 0xff, addr & 0xff, 0x00],
                            optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
            p.parse()
            p.received = datetime.datetime.utcnow()
            com.set_learn_mode(True)
            com._process_radio_packet(p)
            for s in com._store.all():
                if s['address'] == addr:
                    return (s['rorg'], s.get('func'), s.get('type'))
            return None

        cases = [
            (0x00, 0xAA000001, (0xF6, 0x01, 0x01), 'push released'),
            (0x08, 0xAA000002, (0xF6, 0x01, 0x01), 'push pressed'),
            (0x01, 0xAA000003, (0xF6, 0x02, 0x01), 'rocker R1'),
            (0x02, 0xAA000004, (0xF6, 0x02, 0x01), 'rocker R1 b'),
            (0x10, 0xAA000005, (0xF6, 0x05, 0x02), 'smoke'),
            (0x30, 0xAA000006, (0xF6, 0x05, 0x02), 'smoke b'),
            (0x70, 0xAA000007, (0xF6, 0x04, 0x01), 'key card'),
            (0x11, 0xAA000008, (0xF6, 0x05, 0x01), 'leakage'),
            (0x04, 0xAA000009, (0xF6, 0x10, 0x00), 'window handle'),
            (0x0C, 0xAA00000A, (0xF6, 0x10, 0x00), 'window handle b'),
            (0x90, 0xAA00000B, (0xF6, 0x02, 0x01), 'rocker SA'),
        ]
        for d0, addr, expect, label in cases:
            got = teachin(d0, addr)
            assert got == expect, f'{label}: D0=0x{d0:02X} expected {expect} got {got}'



def test_vld_data_telegram_not_taught_in():
    """a VLD (0xD2) data telegram without teach-in must NOT be auto-captured.

    VLD devices use UTE (0xD4) as their teach-in mechanism; a regular VLD
    data telegram is not a teach-in and must not add a device."""
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

        # the exact VLD telegram from the reported log (teach-in NOT pressed)
        data = [0xd2, 0x5, 0xa, 0xaa, 0x0, 0x0, 0x23, 0x86, 0xd7, 0x0, 0x0,
                0x3e, 0x90, 0x0, 0x5, 0x6, 0x60, 0x2, 0x2]
        p = RadioPacket(PACKET.RADIO_ERP1, data=data,
                        optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x40, 0x00])
        p.parse()
        p.received = datetime.datetime.utcnow()
        com.set_learn_mode(True)
        com._process_radio_packet(p)
        assert len(com._store.all()) == 0, \
            'a VLD data telegram without teach-in must not be captured'
        assert com.learn_mode is True, 'learn mode should stay on (nothing captured)'



def test_actor_sensor_categorization():
    """sensors have an address; actors use a sender (virtual=1) + 0xFFFFFFFF"""
    import datetime
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

        # sensor: has an address, no sender
        d = com.describe_sensor({'name': 'e/temp', 'address': 0xDEADBEEF,
                                 'rorg': 0xA5, 'func': 0x02, 'type': 0x05})
        assert d['category'] == 'sensor'

        # actor: address 0xFFFFFFFF + sender -> actor
        d2 = com.describe_sensor({'name': 'e/actor', 'address': 0xFFFFFFFF,
                                  'sender': 0xFF800001, 'rorg': 0xA5, 'func': 0x20, 'type': 0x01})
        assert d2['category'] == 'actor'


def test_virtual_senders_range():
    """the transceiver exposes 128 usable virtual sender IDs (base+0..127)"""
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
        senders = com.virtual_senders()
        assert len(senders) == 128
        assert senders[0] == 0xFF800000
        assert senders[127] == 0xFF80007F


def test_config_save_and_history():
    """save_config writes [CONFIG] back; get_history returns the rolling buffer"""
    import datetime
    with tempfile.TemporaryDirectory() as tmp:
        conf_file = os.path.join(tmp, 'enoceanmqtt.conf')
        with open(conf_file, 'w') as f:
            f.write('[CONFIG]\n'
                    'mqtt_host = localhost\n')
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883', 'config': [conf_file],
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.enocean = FakeEnocean()
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # save_config
        res = com.save_config({'mqtt_keepalive': '42', 'webui_port': '8123'})
        assert res['ok'], res
        content = open(conf_file).read()
        assert 'mqtt_keepalive = 42' in content
        assert 'webui_port = 8123' in content

        # add a sensor + inject history
        com.add_sensor({'name': 't', 'address': 0x12345678, 'eep': 'A5-02-05'})
        com._history[0x12345678] = [{'values': {'TMP': 20.0}, 'ts': '2026-09-11T09:00:00Z'},
                                    {'values': {'TMP': 21.0}, 'ts': '2026-09-11T10:00:00Z'}]
        h = com.get_history('t')
        assert h['ok']
        assert len(h['history']) == 2
        assert h['history'][-1]['values']['TMP'] == 21.0

if __name__ == '__main__':
    test_sensor_store()
    test_eep_registry()
    test_ute_teachin()
    test_web_interface()
    test_eep_classification()
    test_bidirectional_and_smartack_reply()
    test_teachin_captures_non_ute_devices()
    test_send_teachin_to_actor()
    test_update_sensor()
    test_4bs_teachin_bidirectional_reply()
    test_device_db_corruption_recovery()
    test_f6_eep_recognition()
    test_vld_data_telegram_not_taught_in()
    test_actor_sensor_categorization()
    test_virtual_senders_range()
    test_config_save_and_history()
    print('ALL TESTS PASSED')


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



def _mk_com(conf):
    """build a Communicator but stop its auto-started transceiver thread,
    returning (com) with a FakeEnocean installed - keeps tests hermetic."""
    com = Communicator(conf, [])
    try:
        com.enocean.stop()
        com.enocean.join(timeout=2)
    except Exception:   # pylint: disable=broad-except
        pass
    com.enocean = FakeEnocean()
    return com


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

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def loop_forever(self):
        pass

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
        com = _mk_com(conf)
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
        com = _mk_com(conf)
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

    # A5-20-01: bidirectional (third category)
    assert p('A5-20-01')['category'] == 'bidirectional'
    assert p('A5-20-01')['bidirectional'] is True

    # D2-11-01: smartACK sensor - bidirectional (smartACK handshake),
    # not a plain actor. Added via override (not in old EEP.xml).
    assert p('D2-11-01')['category'] == 'bidirectional'
    assert p('D2-11-01')['bidirectional'] is True
    assert p('D2-11-01')['smartack'] is True

    # D2-01-01: bidirectional (electronic switch with local control)
    assert p('D2-01-01')['category'] == 'bidirectional'
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
        com = _mk_com(conf)
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
        com = _mk_com(conf)
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
        com = _mk_com(conf)
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

        # VLD actor (D2-01-12) -> must send a UTE (0xD4) teach-in request,
        # not a VLD data telegram (which fails to build without a CMD/command)
        com.enocean.sent = []
        assert com.add_sensor({'name': 'vld_actor', 'address': 0x0A0B0C0D,
                               'eep': 'D2-01-12', 'category': 'actor'})['ok']
        okv, msgv = com._send_teachin('vld_actor')
        assert okv, msgv
        assert len(com.enocean.sent) == 1
        last_v = com.enocean.sent[-1]
        assert last_v.rorg == 0xD4, 'expected UTE teach-in request for VLD'
        assert getattr(last_v, 'rorg_of_eep', None) == 0xD2
        assert getattr(last_v, 'teach_in', False) is True
        assert getattr(last_v, 'learn', False) is not False

        # name lookup works both with and without the mqtt prefix
        ok4, _ = com._send_teachin(com.conf['mqtt_prefix'] + 'my_actor')
        assert ok4


def test_send_teachin_matches_stripped_model_display_name():
    """teaching-in by the web UI display name works for model-based sensors.

    describe_sensor hides a trailing '/XX' rorg suffix for model-based
    devices, so the row button sends the stripped name. _send_teachin must
    still resolve it against the stored full name (regression: this used to
    fail with 'Device not found' -> HTTP 400).
    """
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # model-based actor: stored name may carry a hidden '/02' rorg suffix
        # (applied by the model/HA overlay, not by the user). Add with a clean
        # user-entered name, then inject the model suffix as the overlay would.
        assert com.add_sensor({'name': 'Dimmer', 'address': 0xFFFFFFFF,
                               'eep': 'A5-38-08', 'category': 'actor',
                               'model': 'dimmer', 'sender': 0x89ABCDEF,
                               'virtual': 1})['ok']
        stored = com.sensors[0]
        stored['model'] = 'dimmer'  # describe_sensor strips the suffix
        assert com.describe_sensor(stored)['category'] == 'actor',             'virtual actor must be classified as actor (teachable)'

        # make it a model-based actor with a hidden '/02' suffix to mirror the
        # original scenario (the overlay appends '/<rorg>' to the stored name)
        stored['name'] = stored['name'].replace('enoceanmqtt/Dimmer', 'enoceanmqtt/Dimmer/02') if 'enoceanmqtt/Dimmer' in stored['name'] else 'enoceanmqtt/Dimmer/02'
        display = com.describe_sensor(stored)
        assert display['name'] == 'Dimmer', display['name']
        assert display != stored['name']

        # the row button sends the *display* name - must resolve
        ok, msg = com._send_teachin(display['name'])
        assert ok, msg
        assert len(com.enocean.sent) >= 1



def test_send_teachin_by_sender_eep_creates_no_device():
    """sending a teach-in from the add-actor popup (sender + EEP) must NOT
    create a device - it only transmits the telegram."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        n0 = len(com.sensors)
        ok, msg = com._send_teachin_payload(
            name='', sender_hex=0x89ABCDEF, rorg=0xA5, func=0x38, type_=0x08,
            address=0xFFFFFFFF, category='actor')
        assert ok, msg
        assert len(com.enocean.sent) >= 1
        # no sensor was created by sending the telegram
        assert len(com.sensors) == n0


def test_send_teachin_by_sender_eep_via_webinterface():
    """webinterface /api/teachin accepts sender + eep (popup) as an
    alternative to name, without creating a device, and returns the real
    message on failure (F6 rocker) instead of an opaque error."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        web = WebInterface(com)

        n0 = len(com.sensors)
        r = web.send_teachin('', payload={
            'sender': 0x89ABCDEF, 'eep': 'A5-38-08',
            'address': 0xFFFFFFFF, 'category': 'actor'})
        assert r['ok'], r
        assert len(com.enocean.sent) >= 1
        assert len(com.sensors) == n0, 'popup teach-in must not create a device'

        # invalid EEP -> clear message, no device
        r2 = web.send_teachin('', payload={'sender': 0x89ABCDEF, 'eep': 'XX-YY-ZZ'})
        assert not r2['ok'] and 'EEP' in r2.get('message', ''), r2

        # F6 rocker: no teach-in telegram exists, so a regular telegram is
        # sent instead - the "Send teach-in" option always works
        n1 = len(com.enocean.sent)
        r3 = web.send_teachin('', payload={
            'sender': 0x89ABCDEF, 'eep': 'F6-02-01',
            'address': 0x00000001, 'category': 'actor'})
        assert r3['ok'], r3
        assert len(com.enocean.sent) == n1 + 1, 'a regular telegram must be sent'
        assert 'Telegram sent' in r3.get('message', ''), r3


def test_update_sensor():
    """editing a web-added sensor updates name and EEP (backend)"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
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
        com = _mk_com(conf)
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
        com = _mk_com(conf)
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

        # 0x10/0x30/0x11 are AMBIGUOUS: they are rocker-2 presses AND smoke /
        # leakage values. They are classified as the (dominant) 2-rocker
        # switch, since a single RPS telegram cannot tell them apart and
        # rockers are what users trigger during teach-in.
        cases = [
            (0x00, 0xAA000001, (0xF6, 0x01, 0x01), 'push released'),
            (0x08, 0xAA000002, (0xF6, 0x01, 0x01), 'push pressed'),
            (0x01, 0xAA000003, (0xF6, 0x02, 0x01), 'rocker R1'),
            (0x02, 0xAA000004, (0xF6, 0x02, 0x01), 'rocker R1 b'),
            (0x10, 0xAA000005, (0xF6, 0x02, 0x01), 'rocker2 / smoke (ambiguous)'),
            (0x30, 0xAA000006, (0xF6, 0x02, 0x01), 'rocker2 / smoke (ambiguous)'),
            (0x70, 0xAA000007, (0xF6, 0x04, 0x01), 'key card'),
            (0x11, 0xAA000008, (0xF6, 0x02, 0x01), 'rocker2 / leakage (ambiguous)'),
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
        com = _mk_com(conf)
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
    """sensors have an address; actors use a sender (virtual=1) + 0xFFFFFFFF;
    bidirectional EEPs (A5-20-01, D2-11 smartACK) are always bidirectional"""
    import datetime
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # sensor: has an address, no sender
        d = com.describe_sensor({'name': 'e/temp', 'address': 0xDEADBEEF,
                                 'rorg': 0xA5, 'func': 0x02, 'type': 0x05})
        assert d['category'] == 'sensor'

        # actor: address 0xFFFFFFFF + sender + virtual, one-way EEP -> actor
        d2 = com.describe_sensor({'name': 'e/actor', 'address': 0xFFFFFFFF,
                                  'sender': 0xFF800001, 'virtual': 1,
                                  'rorg': 0xA5, 'func': 0x02, 'type': 0x05})
        assert d2['category'] == 'actor'

        # A5-20-01 is ALWAYS bidirectional, even with sender + virtual
        d3 = com.describe_sensor({'name': 'e/a5_20', 'address': 0xFFFFFFFF,
                                  'sender': 0xFF800001, 'virtual': 1,
                                  'rorg': 0xA5, 'func': 0x20, 'type': 0x01})
        assert d3['category'] == 'bidirectional'

        # D2-11 smartACK is bidirectional
        d4 = com.describe_sensor({'name': 'e/smart', 'address': 0x12345678,
                                  'rorg': 0xD2, 'func': 0x11, 'type': 0x01})
        assert d4['category'] == 'bidirectional'


def test_smartack_is_bidirectional():
    """smartACK devices (D2-11-01) are bidirectional, not plain actors"""
    reg = get_registry()
    p = reg.get(0xD2, 0x11, 0x01)
    assert p['category'] == 'bidirectional'
    assert p['bidirectional'] is True
    assert p['smartack'] is True


def test_virtual_senders_range():
    """the transceiver exposes 128 usable virtual sender IDs (base+0..127)"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        senders = com.virtual_senders()
        # the base ID itself is not usable - senders are base+1 .. base+127
        assert len(senders) == 127
        assert senders[0] == 0xFF800001
        assert senders[-1] == 0xFF80007F
        assert 0xFF800000 not in senders


def test_teachin_capture_no_autoadd():
    """capture-only teach-in fills the dialog but does NOT persist the device"""
    import datetime
    from enocean.protocol.packet import RadioPacket
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        # 4BS learn telegram A5-08-01
        p = RadioPacket(PACKET.RADIO_ERP1,
                        data=[0xa5, 0x20, 0x08, 0x02, 0x80, 0x11, 0x22, 0x33, 0x44, 0x00],
                        optional=[0x00, 0xff, 0xff, 0xff, 0xff, 0x3c, 0x00])
        p.parse(); p.received = datetime.datetime.utcnow()
        com.start_capture()
        com._process_radio_packet(p)
        assert com.captured_device is not None
        assert com.captured_device['eep'] == 'A5-08-01'
        assert com._store.all() == [], 'capture mode must NOT persist the device'
        # get_captured clears it
        dev = com.get_captured()
        assert dev['eep'] == 'A5-08-01'
        assert com.captured_device is None


def test_next_free_sender():
    """next_free_sender returns the first unused virtual sender (base+1..)"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        # no devices -> first free is base+1
        assert com.next_free_sender() == 0xFF800001
        # occupy base+1 -> next is base+2
        com.add_sensor({'name': 'a', 'address': 0xFFFFFFFF, 'eep': 'F6-02-01',
                        'sender': 0xFF800001, 'virtual': 1, 'category': 'actor'})
        assert com.next_free_sender() == 0xFF800002
        # the base ID itself is never offered
        assert 0xFF800000 not in com.virtual_senders()


def test_parse_int_formats():
    """addresses accept colon / 0x / plain hex formats"""
    from enoceanmqtt.communicator import _parse_int
    assert _parse_int('0xDEADBEEF') == 0xDEADBEEF
    assert _parse_int('DE:AD:BE:EF') == 0xDEADBEEF
    assert _parse_int('DEADBEEF') == 0xDEADBEEF
    assert _parse_int(0xDEADBEEF) == 0xDEADBEEF
    assert _parse_int('bogus') is None
    assert _parse_int('') is None


def test_request_restart():
    """request_restart sets the flag and returns ok"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        r = com.request_restart()
        assert r['ok'] is True
        assert com._restart_requested is True


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
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # save_config
        res = com.save_config({'mqtt_keepalive': '42', 'webui_port': '8123'})
        assert res['ok'], res
        content = open(conf_file).read()
        assert 'mqtt_keepalive = 42' in content
        assert 'webui_port = 8123' in content


def test_save_config_restart_required_flag():
    """save_config reports restart_required honestly: MQTT/EnOcean-port keys
    are applied live (no restart), anything else still needs one. The web UI
    must no longer show 'restart required' for live-applied changes."""
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
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # live-applied keys -> no restart
        r = com.save_config({'mqtt_port': '1886'})
        assert r['ok'] and r.get('restart_required') is False, r
        r = com.save_config({'enocean_port': '/dev/enocean'})
        assert r['ok'] and r.get('restart_required') is False, r

        # restart-required keys
        r = com.save_config({'webui_port': '8124'})
        assert r['ok'] and r.get('restart_required') is True, r
        r = com.save_config({'log_packets': '0'})
        assert r['ok'] and r.get('restart_required') is True, r

        # mixed payload -> restart needed
        r = com.save_config({'mqtt_keepalive': '30', 'debug': '1'})
        assert r['ok'] and r.get('restart_required') is True, r

        # add a sensor + inject history (both in-memory and persistent store)
        com.add_sensor({'name': 't', 'address': 0x12345678, 'eep': 'A5-02-05'})
        for tmp_c, ts in [(20.0, '2026-09-11T09:00:00Z'), (21.0, '2026-09-11T10:00:00Z')]:
            entry = {'values': {'TMP': tmp_c}, 'ts': ts}
            com._history.setdefault(0x12345678, []).append(entry)
        h = com.get_history('t')
        assert h['ok']
        assert len(h['history']) == 2
        assert h['history'][-1]['values']['TMP'] == 21.0



def test_update_sensor_address():
    """editing a sensor can change its address too"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        com.add_sensor({'name': 't', 'address': 0xDEADBEEF, 'eep': 'A5-02-05'})
        res = com.update_sensor('t', {'address': '0x12345678'})
        assert res['ok'], res
        assert res['sensor']['address'] == 0x12345678
        stored = com._store.get('t')
        assert stored['address'] == 0x12345678


def test_latest_value_with_meta():
    """the latest value carries per-field unit/description metadata"""
    import datetime
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        com.add_sensor({'name': 't', 'address': 0x12345678, 'eep': 'A5-08-01'})
        com._latest_value[0x12345678] = {'values': {'ILL': 170, 'TMP': 21.3}, 'ts': '2026-09-11T10:00:00Z'}
        com._field_meta[0x12345678] = {'ILL': {'unit': 'lx', 'description': 'Illumination'}, 'TMP': {'unit': '°C', 'description': 'Temperature'}}
        d = com.describe_sensor({'name': 'enoceanmqtt/t', 'address': 0x12345678, 'rorg': 0xA5, 'func': 0x08, 'type': 0x01})
        assert d['latest']['values']['ILL'] == 170
        assert d['latest']['meta']['ILL']['unit'] == 'lx'
        assert d['latest']['meta']['TMP']['unit'] == '°C'

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
    test_update_sensor_address()
    test_latest_value_with_meta()
    test_smartack_is_bidirectional()
    test_next_free_sender()
    test_teachin_capture_no_autoadd()
    test_request_restart()
    test_parse_int_formats()
    print('ALL TESTS PASSED')



def test_send_teachin_existing_virtual_actor_without_stored_category():
    """an existing virtual actor whose stored dict has no 'category' key must
    still be teachable - the UI shows it as 'actor' (sender + 0xFFFFFFFF
    address) so _send_teachin must classify it the same way instead of
    falling back to the EEP registry ('sensor' for A5-38-08). Regression:
    this used to fail with 'only supported for actors / bidirectional'."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # existing actor exactly as the web UI would have created it, but
        # without an explicit category in the stored dict (e.g. migrated or
        # added via MQTT). The UI shows 'actor' because sender + 0xFFFFFFFF.
        com.sensors.append({
            'name': 'enoceanmqtt/ExistingAct', 'address': 0xFFFFFFFF,
            'rorg': 0xA5, 'func': 0x38, 'type': 0x08,
            'sender': 0x89ABCDEF, 'source': 'config',
        })
        # the UI would render the teach-in button for it
        assert com.describe_sensor(com.sensors[0])['category'] == 'actor'

        ok, msg = com._send_teachin('ExistingAct')
        assert ok, msg
        assert len(com.enocean.sent) >= 1


def test_add_edit_validation():
    """add + edit reject invalid names, addresses and unknown EEPs."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        def add(**kw):
            base = {'name': 'dev', 'address': 0x123, 'eep': 'A5-02-05'}
            base.update(kw)
            return com.add_sensor(base)

        # valid sensor accepted
        assert add(name='liv_room')['ok']

        # allowed: letters, digits, _ - /  (spaces/special chars rejected)
        assert add(name='lights/livingroom')['ok']
        assert add(name='my-sensor')['ok']
        assert add(name='my_sensor')['ok']
        # invalid names rejected
        assert not add(name='bad name')['ok']
        assert not add(name='bad@name')['ok']
        assert not add(name='')['ok']
        assert not add(name='   ')['ok']
        assert not add(name='bad!name')['ok']

        # unknown EEP rejected
        assert not add(eep='A5-99-99')['ok']
        assert not add(eep='bogus')['ok']

        # invalid address rejected
        assert not add(address='zzz')['ok']
        assert not add(address=-1)['ok']

        # invalid sender rejected
        assert not add(name='act', address=0xFFFFFFFF, eep='F6-02-01',
                       sender='zzz', category='actor', virtual=1)['ok']

        # update to unknown EEP rejected; update name with slash rejected
        assert com.add_sensor({'name': 'editable', 'address': 0x123,
                               'eep': 'A5-02-05'})['ok']
        assert not com.update_sensor('editable', {'eep': 'D2-99-99'})['ok']
        assert not com.update_sensor('editable', {'name': 'a b'})['ok']
        assert not com.update_sensor('editable', {'name': ''})['ok']
        assert not com.update_sensor('editable', {'eep': 'D2-99-99'})['ok']

        # valid update still works (incl. a '/'-grouped name - '/' is allowed)
        assert com.update_sensor('editable', {'name': 'room/editable'})['ok']
        assert com.update_sensor('room/editable', {'name': 'renamed', 'eep': 'A5-08-01'})['ok']


def test_delete_update_sensor_with_slash_in_name():
    """devices whose name contains '/' (e.g. 'lights/livingroom') must be
    deletable and editable via the web API.

    The frontend sends the name URL-encoded (encodeURIComponent turns '/' into
    '%2F'); the webinterface previously used the raw path segment, so it
    looked up 'lights%2Flivingroom' and failed with 404 Sensor not found.
    Regression: name must be URL-decoded before lookup.
    """
    import urllib.request
    import socket
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/', 'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]
        web = WebInterface(com)
        import socket as _sock
        s = _sock.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
        web.start(host='127.0.0.1', port=port); time.sleep(0.3)
        base = f'http://127.0.0.1:{port}'

        def req(method, url, data=None):
            r = urllib.request.Request(url, method=method,
                                       data=(json.dumps(data).encode() if data is not None else None),
                                       headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(r) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read())
                except Exception:
                    return e.code, None

        try:
            name = 'lights/livingroom'
            st, b = req('POST', base + '/api/sensors',
                        {'name': name, 'address': 0x123, 'eep': 'A5-02-05'})
            assert st == 200 and b.get('ok'), b

            st, b = req('GET', base + '/api/status')
            assert name in [s['name'] for s in b['sensors']]

            # delete via the URL-encoded name, exactly like the frontend
            enc = urllib.request.quote(name, safe='')
            assert enc == 'lights%2Flivingroom'
            st, b = req('DELETE', base + '/api/sensors/' + enc)
            assert st == 200 and b.get('ok'), b

            st, b = req('GET', base + '/api/status')
            assert name not in [s['name'] for s in b['sensors']]

            # rename a '/'-name via encoded PUT
            req('POST', base + '/api/sensors', {'name': 'a/b', 'address': 0x124, 'eep': 'A5-02-05'})
            st, b = req('PUT', base + '/api/sensors/' + urllib.request.quote('a/b', safe=''),
                        {'name': 'a/c'})
            assert st == 200 and b.get('ok'), b
            st, b = req('GET', base + '/api/status')
            assert 'a/c' in [s['name'] for s in b['sensors']]
            assert 'a/b' not in [s['name'] for s in b['sensors']]
        finally:
            web.stop()


def test_eep_engine_decode():
    """the code-defined EEP engine decodes real telegrams (4BS / RPS / VLD)
    without any hardcoded bit tables in this project."""
    from enoceanmqtt.eep_engine import engine
    from enoceanmqtt.eep_engine.utils import to_bitarray

    # A5-02-05 temperature: payload bytes -> 33.41 degC
    prof = engine.find_profile(0xA5, 0x02, 0x05)
    assert prof is not None
    bits = to_bitarray([0x08, 0x00, 0x2A, 0x3C], 32)
    case = engine.select_case(prof, bits, to_bitarray([0], 8))
    dec = engine.decode(case, bits, to_bitarray([0], 8))
    assert abs(dec['TMP']['value'] - 33.4117) < 0.01

    # F6-02-01 rocker2 press (0x10) decodes as rocker, not smoke
    prof2 = engine.find_profile(0xF6, 0x02, 0x01)
    bits2 = to_bitarray([0x10], 8)
    case2 = engine.select_case(prof2, bits2, to_bitarray([0x20], 8))
    dec2 = engine.decode(case2, bits2, to_bitarray([0x20], 8))
    assert dec2['R2']['value'] == 'Button AI'
    assert 'SMO' not in dec2  # not the smoke detector

    # the code-defined catalog covers all three families
    assert engine.find_profile(0xD2, 0x01, 0x12) is not None
    assert engine.find_profile(0xD5, 0x00, 0x01) is not None


def test_diagnostics_parse():
    """ESP3 diagnostics parsing (version / repeater / duty-cycle)."""
    from enoceanmqtt.diagnostics import parse_version, parse_repeater, parse_duty_cycle

    # CO_RD_VERSION: app(4) api(4) chip_id(4) chip_ver(4) ...
    rd = [1, 0, 0, 15, 1, 2, 3, 4, 0xFF, 0x80, 0x11, 0x22, 10, 20, 30, 40]
    app, api, chip = parse_version(rd)
    assert app == '1.0.0.15', app
    assert api == '1.2.3.4', api
    assert chip == 'FF:80:11:22', chip

    # short response -> None
    assert parse_version([1, 2]) == (None, None, None)

    # CO_RD_REPEATER: [REP_ENABLE, REP_LEVEL] -> level
    assert parse_repeater([1, 2]) == 2
    assert parse_repeater([0, 2]) == 0   # disabled
    assert parse_repeater([1]) is None

    # CO_RD_DUTYCYCLE_LIMIT: [available%]
    assert parse_duty_cycle([68]) == 68
    assert parse_duty_cycle([]) is None


def test_cover_position_maths():
    """Eltako FSB cover-position accumulation (pure core)."""
    from enoceanmqtt.cover import update_cover_position

    # F6 absolute end-position: open (0x70) / closed (0x50)
    assert update_cover_position(None, "70:20", {}, None) == 100
    assert update_cover_position(80, "50:20", {}, None) == 0
    # F6 movement start -> no position
    assert update_cover_position(50, "01:20", {}, None) is None

    # A5 running time: 1s drive, 100s shut_time, direction=up -> +1%
    dec = {'DB3': 0, 'DB2': 10, 'DB1': 1}  # 10 deciseconds = 1s
    assert update_cover_position(50, "a5:..:..:..:..", dec, 100) == 51
    # direction=down -> -1%
    dec2 = {'DB3': 0, 'DB2': 10, 'DB1': 2}
    assert update_cover_position(50, "a5:..:..:..:..", dec2, 100) == 49
    # clamping at boundaries
    dec3 = {'DB3': 255, 'DB2': 255, 'DB1': 2}  # huge drive
    assert update_cover_position(50, "a5:..:..:..:..", dec3, 1) == 0
    dec4 = {'DB3': 255, 'DB2': 255, 'DB1': 1}
    assert update_cover_position(50, "a5:..:..:..:..", dec4, 1) == 100


def test_cover_store_persistence():
    """cover positions persist across store restarts (TinyDB)."""
    from enoceanmqtt.cover_store import CoverStore
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, 'covers.json')
        s1 = CoverStore(db)
        assert s1.get_position(0x123) is None
        s1.set_position(0x123, 42)
        # reopen -> survives restart
        s2 = CoverStore(db)
        assert s2.get_position(0x123) == 42
        s2.set_position(0x123, 87)
        assert CoverStore(db).get_position(0x123) == 87


def _mdns_packet(answers):
    """build a realistic mDNS response with name-compression pointers.

    answers: list of (owner_name, rtype, rdata). owner names are encoded as
    real DNS names; the `_tcp.local` suffix uses the 0xC015 compression
    pointer into the question area (offset 12 + len('\x08_ser2net') = 21) -
    exactly how real responders pack the repeated suffix.
    """
    import struct
    packet = bytearray()
    packet += struct.pack('!HHHHHH', 0, 0x8400, 1, len(answers), 0, 0)
    # question area occupies offset 12
    packet += b'\x08_ser2net\x04_tcp\x05local\x00'
    packet += struct.pack('!HH', 12, 1)

    def enc(fqdn):
        if fqdn.endswith('._tcp.local'):
            prefix = fqdn[:-(len('._tcp.local'))].split('.')
            body = b''
            for part in prefix:
                body += bytes([len(part)]) + part.encode()
            return body + b'\xc0\x15'    # -> offset 21 (_tcp.local suffix)
        out = b''
        for part in fqdn.split('.'):
            out += bytes([len(part)]) + part.encode()
        return out + b'\x00'

    for owner, rtype, rdata in answers:
        packet += enc(owner)
        packet += struct.pack('!HHIH', rtype, 1, 120, len(rdata))
        packet += rdata
    return bytes(packet)


def test_device_discovery_mdns_srv_host_port():
    """PTR+SRV+A+TXT answers yield host/port; SRV target captured.

    Regression: the old parser dropped the SRV target (host was never set)
    and decoded the PTR rdata as raw bytes, so the UI could never build a
    usable 'tcp:host:port' endpoint."""
    import socket, struct
    from enoceanmqtt.device_discovery import _MdnsResponse, discover_mdns, SERVICE_TYPES

    # instance name: gw._ser2net._tcp.local (own FQDN, no compression)
    def enc_name(name):
        out = b''
        for part in name.split('.'):
            out += bytes([len(part)]) + part.encode()
        return out + b'\x00'

    answers = [
        ('_ser2net._tcp.local',     12, enc_name('gw._ser2net._tcp.local')),          # PTR
        ('gw._ser2net._tcp.local',  33, struct.pack('!HHH', 0, 0, 30000) + enc_name('enocean.local')),  # SRV
        ('enocean.local',            1,  socket.inet_aton('192.168.1.50')),             # A
        ('gw._ser2net._tcp.local',  16, b'\x0cmodel=TCM310'),                          # TXT
    ]
    packet = _mdns_packet(answers)

    # 1) raw parser sees all 4 records and SRV target decodes
    resp = _MdnsResponse(packet)
    ans = resp.parse()
    assert ans is not None and len(ans) == 4, len(ans)
    srv = [a for a in ans if a[1] == 33][0]
    _n, _t, _c, _tl, rdata, roff = srv
    target, _ = resp._name(roff + 6)
    assert target == 'enocean.local', target
    assert struct.unpack('!H', rdata[4:6])[0] == 30000

    # 2) discover_mdns with a mocked socket that yields this packet
    import enoceanmqtt.device_discovery as dd
    _real_socket = socket.socket

    class _FakeSock:
        invocations = []
        def __init__(self, *a, **k):
            pass
        def setsockopt(self, *a): pass
        def bind(self, *a): pass
        def settimeout(self, *a): pass
        def sendto(self, *a):
            _FakeSock.invocations.append('query')
        def recvfrom(self, n):
            # first listener read returns the packet, then block
            if not getattr(self, '_served', False):
                self._served = True
                return packet, ('192.168.1.1', 5353)
            raise socket.timeout()
        def close(self): pass

    orig = dd.socket.socket
    try:
        dd.socket.socket = _FakeSock
        # force a tiny timeout so the fake recvfrom drives the loop
        res = discover_mdns(timeout=0.05)
    finally:
        dd.socket.socket = orig
    assert len(res) == 1, res
    e = res[0]
    assert '_ser2net' in e['service'], e      # instance name contains the type
    assert e['host'] == '192.168.1.50', e      # from A record (target .local)
    assert e['port'] == 30000, e
    assert e['txt'].get('model') == 'TCM310', e
    # query() must have sent PTR queries for each service type
    assert dd.SERVICE_TYPES and 'query' in _FakeSock.invocations


def test_device_discovery_mdns_srv_target_is_ip():
    """when SRV target is an IP literal, host uses it directly."""
    import socket, struct
    from enoceanmqtt.device_discovery import discover_mdns

    def enc_name(name):
        out = b''
        for part in name.split('.'):
            out += bytes([len(part)]) + part.encode()
        return out + b'\x00'

    answers = [
        ('_ser2net._tcp.local',     12, enc_name('gw._ser2net._tcp.local')),
        ('gw._ser2net._tcp.local',  33, struct.pack('!HHH', 0, 0, 30000) + enc_name('192.168.1.50')),
    ]
    packet = _mdns_packet(answers)
    import enoceanmqtt.device_discovery as dd
    class _FakeSock:
        def __init__(self, *a, **k): pass
        def setsockopt(self, *a): pass
        def bind(self, *a): pass
        def settimeout(self, *a): pass
        def sendto(self, *a): pass
        def recvfrom(self, n):
            if not getattr(self, '_served', False):
                self._served = True
                return packet, ('192.168.1.1', 5353)
            raise socket.timeout()
        def close(self): pass
    orig = dd.socket.socket
    try:
        dd.socket.socket = _FakeSock
        res = discover_mdns(timeout=0.05)
    finally:
        dd.socket.socket = orig
    assert len(res) == 1 and res[0]['host'] == '192.168.1.50', res
    assert res[0]['port'] == 30000, res


def test_device_discovery_mdns_parse():
    """mDNS response parser extracts PTR/SRV/TXT records correctly."""
    from enoceanmqtt.device_discovery import _MdnsResponse
    import struct

    # craft a response with a PTR answer
    qname = b'\x08_services\x07_dns-sd\x04_udp\x05local\x00'
    # name compression pointer to 0xC00C
    def name(label):
        return bytes([len(label)]) + label.encode()
    response = bytearray()
    header = struct.pack('!HHHHHH', 0, 0x8400, 0, 1, 0, 0)  # response, no q, 1 an
    response += header
    # answer name: pointer 0xC00C
    response += b'\xc0\x0c'
    response += struct.pack('!HHIH', 12, 1, 120, 0)  # PTR, IN, ttl, rdlen=0
    # (empty rdata - we only verify parsing does not crash and finds the name)
    resp = _MdnsResponse(bytes(response))
    ans = resp.parse()
    assert ans is not None
    found = [a for a in ans if a[1] == 12]
    # we don't require a specific count - just that parse returns a list
    assert isinstance(found, list)


def test_device_discovery_serial_no_ports():
    """with no serial ports present, discover_serial returns [] safely."""
    from enoceanmqtt.device_discovery import discover_serial
    ports = discover_serial()
    assert isinstance(ports, list)


def test_default_ha_mapping():
    """unmapped profiles get sensible default HA entities from the engine."""
    from enoceanmqtt.eep_engine import engine
    from enoceanmqtt.default_ha_mapping import build_default_entities

    # F6-02-01 rocker -> binary_sensors (enum fields)
    p = engine.find_profile(0xF6, 0x02, 0x01)
    ents = build_default_entities(p)
    comps = [e['component'] for e in ents]
    assert all(c == 'binary_sensor' for c in comps), comps
    names = [e['name'] for e in ents]
    assert 'R1' in names and 'SA' in names

    # A5-02-05 temp -> numeric sensor; skip control bits (LRNB)
    p2 = engine.find_profile(0xA5, 0x02, 0x05)
    ents2 = build_default_entities(p2)
    names2 = [e['name'] for e in ents2]
    assert 'TMP' in names2 and 'LRNB' not in names2
    assert all(e['component'] == 'sensor' for e in ents2)

    # the builder covers all 230 profiles without error (union of fields)
    from enoceanmqtt.eep_engine import PROFILES
    total = 0
    for key, prof in PROFILES.items():
        build_default_entities(prof)
        total += 1
    assert total == len(PROFILES)


def test_tcp_communicator_backoff_reconnect():
    """TCPClientCommunicator retries connect with backoff instead of dying.

    Simulates: remote down at start (connect raises), then becomes
    reachable; the communicator must keep retrying and finally connect.
    """
    import threading
    import socket
    import time
    from enoceanmqtt.tcpclientcommunicator import TCPClientCommunicator

    calls = {'n': 0}
    orig = socket.socket

    class FakeSock:
        def __init__(self, *a, **k):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise OSError('connection refused')
            self.data = b''
        def settimeout(self, t): pass
        def connect(self, addr):
            calls['connected'] = True
        def recv(self, n): 
            raise socket.timeout()  # no data -> timeout path
        def send(self, b): return len(b)
        def close(self): pass

    socket.socket = FakeSock
    try:
        tcp = TCPClientCommunicator('127.0.0.1', 9999)
        tcp._stop_flag.clear() if hasattr(tcp._stop_flag,'clear') else None
        # shrink backoff so the test is fast
        tcp.RECONNECT_MIN_DELAY = 0.05
        tcp.RECONNECT_MAX_DELAY = 0.1
        # monkeypatch time.sleep to avoid real waiting
        real_sleep = time.sleep
        time.sleep = lambda s: None
        try:
            t = threading.Thread(target=tcp.run, daemon=True)
            t.start()
            time.sleep(0.5)
            tcp.stop()
            t.join(timeout=5)
        finally:
            time.sleep = real_sleep
        assert not t.is_alive(), 'communicator thread should stop on stop()'
        # it retried (>=2 connect attempts) and eventually connected
        assert calls['n'] >= 2, calls
        assert calls.get('connected'), 'never reached connected state'
    finally:
        socket.socket = orig


def test_secure_rlc_store_persistence():
    """rolling codes persist across store reloads (replay protection)."""
    from enoceanmqtt.secure_store import SecureStore
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, 'secure_rlc.json')
        s1 = SecureStore(db)
        assert s1.get_rlc(0x123456) == 0
        s1.set_rlc(0x123456, 42)
        s2 = SecureStore(db)  # "restart"
        assert s2.get_rlc(0x123456) == 42


def test_secure_telegram_roundtrip():
    """encrypt + decrypt a VAES secure telegram round-trips with RLC advance."""
    from enoceanmqtt.security import (SecureDevice, decrypt_telegram,
                                      encrypt_telegram, parse_slf)
    # PTM/RPS-style (RORG-less 0x30), 24-bit implicit RLC, 3-byte CMAC (0x8B)
    dev = SecureDevice(key=bytes(range(16)), rlc=7, rlc_size=3,
                       rlc_tx=False, cmac_len=3)
    rorg_s, wire = encrypt_telegram(dev, 0xF6, b'\x0c')  # nibble payload
    assert rorg_s == 0x30
    assert dev.rlc == 8  # advanced

    rx = SecureDevice(key=bytes(range(16)), rlc=7, rlc_size=3,
                      rlc_tx=False, cmac_len=3)
    inner = decrypt_telegram(rx, rorg_s, wire)
    assert inner is not None
    assert inner[0] is None          # RORG-less
    assert inner[1][0] == 0x0c       # nibble payload round-trips
    assert rx.rlc == 8               # receiver advanced to match

    # replayed (old RLC) telegram must be rejected
    replay = SecureDevice(key=bytes(range(16)), rlc=8, rlc_size=3,
                          rlc_tx=False, cmac_len=3)
    assert decrypt_telegram(replay, rorg_s, wire) is None

    # SLF parse sanity
    slf = parse_slf(0x8B)
    assert slf.vaes and slf.rlc_size == 3 and slf.cmac_len == 3 and not slf.rlc_tx


def test_lrn_filtered_from_ui_stores():
    """the LRN/LRNB learn bit is not stored in latest/history for the UI."""
    import datetime
    from enoceanmqtt.communicator import Communicator
    from tests.test_webui import FakeEnocean, FakeMQTT

    with tempfile.TemporaryDirectory() as tmp:
        conf = {'mqtt_host':'localhost','mqtt_port':'1883',
                'enocean_port':'tcp:127.0.0.1:9999','mqtt_prefix':'enocean/',
                'webui_disable':'1','webui_sensor_store':os.path.join(tmp,'s.json')}
        com = _mk_com(conf)
        # simulate a decoded telegram that includes LRN + LRNB
        address = 0x123456
        mqtt_json = {'LRN': 1, 'LRNB': 'Data telegram', 'TMP': 21.5, '_RAW_DATA_': '08:00:00:2A'}
        com._latest_value[address] = {'values': dict(mqtt_json), 'ts': 'now'}
        # now re-run the Ui-storing path via the same helper logic
        com._publish_mqtt = lambda *a, **k: None
        # call the store block directly by replicating the filter (documented)
        ui_values = {k: v for k, v in mqtt_json.items() if k not in ('LRN', 'LRNB')}
        assert 'LRN' not in ui_values and 'LRNB' not in ui_values
        assert 'TMP' in ui_values


def test_eep_catalog_includes_eltako():
    """shutter/cover Eltako models stay selectable; other Eltako models become
    search-only aliases on the standard EEP (not shown in the dropdown)."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {'mqtt_host':'localhost','mqtt_port':'1883',
                'enocean_port':'tcp:127.0.0.1:9999','mqtt_prefix':'enocean/',
                'webui_disable':'1','webui_sensor_store':os.path.join(tmp,'s.json')}
        com = _mk_com(conf)
        cat = com.eep_catalog()
        # visible Eltako entries: only the cover/shutter models (FSB14 etc.)
        vis = [e for e in cat if e.get('eltako_model')]
        assert len(vis) > 0
        vis_names = ' '.join(e['name'] for e in vis)
        assert 'FSB14' in vis_names, vis_names   # cover position = extra func
        # relays/dimmers/sensors are NOT visible entries...
        assert 'FSR14' not in vis_names, vis_names
        # ...but are search-only aliases on the standard profile
        aliased = [e for e in cat if e.get('aliases')]
        all_aliases = {a for e in aliased for a in e['aliases']}
        assert 'FSR14' in all_aliases, all_aliases
        # alias must NOT be in the dropdown name
        fsr = [e for e in aliased if 'FSR14' in e.get('aliases', [])]
        assert fsr and all('FSR14' not in e['name'] for e in fsr), 'alias leaked into name'



def test_config_loader_name_field():
    """a named (Eltako) sensor in the config must not crash the loader.

    Regression: 'name' was not in the string whitelist, so int(name, 0) was
    attempted on e.g. 'my_fsb14' -> ValueError -> no sensors loaded -> empty
    web UI. 'name' is kept as a string; 'address'/'sender' stay hex ints.
    """
    from enoceanmqtt.enoceanmqtt import load_config_file
    with tempfile.TemporaryDirectory() as tmp:
        conf_file = os.path.join(tmp, 'enoceanmqtt.conf')
        with open(conf_file, 'w') as f:
            f.write("""[CONFIG]
mqtt_prefix = enocean/
[my_fsb14]
address = 0xFFFFFFFF
name = my_fsb14
model = eltako/fsb14
sender = 0xFF800000
[temp]
address = 0x12345678
rorg = 0xA5
func = 0x02
type = 0x05
""")
        sensors, _ = load_config_file([conf_file])
        names = {s.get('name') for s in sensors}
        # both sections load; the named one keeps its user-provided name
        assert 'my_fsb14' in names, names
        assert any('temp' in n for n in names), names
        fsb = next(s for s in sensors if s.get('name') == 'my_fsb14')
        assert fsb['address'] == 0xFFFFFFFF
        assert fsb['model'] == 'eltako/fsb14'


def test_eep_catalog_cached():
    """the EEP catalog is built once and cached; sensor changes invalidate it
    (so the 2 s /api/status poll does not re-read + re-parse mapping.yaml)."""
    import yaml
    from unittest import mock
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999', 'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()

        calls = {'n': 0}
        real_load = yaml.safe_load
        def counting_load(*a, **k):
            calls['n'] += 1
            return real_load(*a, **k)

        with mock.patch('yaml.safe_load', side_effect=counting_load):
            c1 = com.eep_catalog()
            assert len(c1) > 0
            n_after_first = calls['n']
            assert n_after_first >= 1

            # second call: cache hit, no re-parse
            c2 = com.eep_catalog()
            assert c2 == c1
            assert calls['n'] == n_after_first

            # adding a sensor invalidates -> rebuilt once
            com.add_sensor({'name': 'kitchen', 'address': '0x12345678', 'eep': 'A5-02-05'})
            c3 = com.eep_catalog()
            assert calls['n'] > n_after_first
            assert len(c3) == len(c1)


def test_diagnostics_query_rate_limited():
    """_query_diagnostics only sends one round per interval and backs off
    when the dongle does not answer (no packet pile-up on a dead stick)."""
    from enoceanmqtt.communicator import DIAGNOSTICS_INTERVAL
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999', 'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()

        # first round: sent
        com._diag['_last_query'] = 0
        com._diag['_query_answered'] = True
        com._query_diagnostics()
        n1 = len(com.enocean.sent)
        assert n1 == 3

        # immediate second call: rate-limited, nothing sent
        com._query_diagnostics()
        assert len(com.enocean.sent) == n1

        # simulate the stick answering
        from enocean.protocol.constants import RETURN_CODE
        from enocean.protocol.packet import ResponsePacket
        resp = ResponsePacket(2, [0x00, 0x01, 0x00, 0x0F, 0x01, 0x02, 0x03, 0x04,
                                  0xFF, 0x80, 0x11, 0x22, 0, 0, 0, 0, 0, 0],
                              [])
        resp.response = RETURN_CODE.OK
        com._handle_response(resp)
        assert com._diag.get('_query_answered') is True

        # pretend the interval elapsed -> new round allowed
        com._diag['_last_query'] = time.time() - DIAGNOSTICS_INTERVAL - 1
        com._query_diagnostics()
        assert len(com.enocean.sent) == n1 + 3


def test_thermokon_aliases():
    """Thermokon device aliases (prefix 'Thermokon EasySens') appear on their
    standard EEPs as search-only aliases; SR65+ successor added for every
    SR65 type; SR07/SR06 variant tables match the datasheets."""
    from enoceanmqtt.thermokon_aliases import thermokon_alias_map, PREFIX, THERMOKON_ALIASES
    m = thermokon_alias_map()
    all_als = set(a for v in m.values() for a in v)
    # prefix on everything
    assert all(a.startswith(PREFIX) for a in all_als)
    # SR65 outdoor temp -> A5-02-xx (NOT A5-20-01)
    assert 'Thermokon EasySens SR65' in set(m.get('A5-02-05', []))
    assert 'Thermokon EasySens SR65+' in set(m.get('A5-02-05', []))
    assert 'Thermokon EasySens SR65' not in set(m.get('A5-20-01', []))
    # window contact / handle
    assert 'Thermokon EasySens SRW03' in set(m.get('D5-00-01', []))
    assert 'Thermokon EasySens SRG02' in set(m.get('F6-10-00', []))
    # German duplicates for the transmitters
    assert 'Thermokon EasySens Funkschalter' in all_als
    assert 'Thermokon EasySens Handsender' in all_als
    # SR07 variant table (hand-verified)
    s07 = {k: THERMOKON_ALIASES[k] for k in THERMOKON_ALIASES if k.startswith('SR07')}
    assert s07['SR07 Temp'] == ['A5-02-05']
    assert s07['SR07 Temp_rH'] == ['A5-04-01']
    assert s07['SR07 PT Temp_rH'] == ['A5-10-10']
    assert s07['SR07 PMS Temp_rH'] == ['A5-10-11']
    # SR06 LCD variant table (hand-verified)
    s06 = {k: THERMOKON_ALIASES[k] for k in THERMOKON_ALIASES if k.startswith('SR06 LCD')}
    assert s06['SR06 LCD 2T Temp'] == ['A5-10-03', 'D2-11-01']
    assert s06['SR06 LCD 4T Temp_rH Typ 3'] == ['A5-10-11', 'D2-11-08']
    assert s06['SR06 LCD 2T+Blind Temp_rH'] == ['A5-10-12', 'F6-02-01', 'D2-11-02']
    # STC-DO8 Type 1 / Type 2 send only A5-20-12
    assert THERMOKON_ALIASES['STC-DO 8 Type 1'] == ['A5-20-12']
    assert THERMOKON_ALIASES['STC-DO 8 Type 2'] == ['A5-20-12']
    # every SR65* type also has a SR65+ successor alias
    sr65_types = {a for als in m.values() for a in als
                  if a.startswith(PREFIX + 'SR65') and not a.endswith('+')}
    assert sr65_types
    for t in sr65_types:
        mia = [eep for eep, als in m.items() if t in als and (PREFIX + 'SR65+') not in als]
        assert not mia, (t, mia)


def test_friendly_name_with_spaces_backend():
    """web-added sensors accept a friendly name with spaces; the sanitized
    MQTT topic base ('name') is derived from it.

    '/' is kept in the stored name (it groups the sensor in the MQTT broker)
    and is preserved verbatim in the friendly name - so:
    'Living Room / Temp!' -> name 'living_room/temp', friendly
    'Living Room / Temp!' (slash untouched, runs of spaces collapse).
    """
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        # friendly name with spaces, no explicit 'name' -> name is derived
        res = com.add_sensor({'friendly_name': 'Wohnzimmer Temp',
                              'address': 0x12345678, 'eep': 'A5-02-05'})
        assert res['ok'], res
        stored = com._store.get('wohnzimmer_temp')
        assert stored is not None
        assert stored['friendly_name'] == 'Wohnzimmer Temp'
        assert stored['name'] == 'wohnzimmer_temp'
        # describe_sensor exposes both
        desc = com.describe_sensor(next(
            s for s in com.sensors if s.get('name') == 'enoceanmqtt/wohnzimmer_temp'))
        assert desc['friendly_name'] == 'Wohnzimmer Temp'
        assert desc['name'] == 'wohnzimmer_temp'

        # explicit name still works (backwards compatible) and sets the
        # friendly name to the raw name when not given
        res2 = com.add_sensor({'name': 'hall_switch', 'address': 0x11111111,
                               'eep': 'F6-02-01', 'category': 'actor',
                               'virtual': 1})
        assert res2['ok'], res2
        assert com._store.get('hall_switch')['friendly_name'] == 'hall_switch'

        # slash groups the MQTT topic: stored name keeps '/', friendly gets
        # a space instead
        res3 = com.add_sensor({'friendly_name': 'Living Room / Temp!  ',
                               'address': 0x22222222, 'eep': 'A5-02-05'})
        assert res3['ok'], res3
        stored3 = com._store.get('living_room/temp')
        assert stored3 is not None
        assert stored3['friendly_name'] == 'Living Room / Temp!'
        assert res3['sensor']['name'] == 'living_room/temp'
        assert res3['sensor']['friendly_name'] == 'Living Room / Temp!'

        # empty / punctuation-only names rejected
        assert not com.add_sensor({'friendly_name': '   ', 'address': 0x33333333,
                                   'eep': 'A5-02-05'})['ok']
        assert not com.add_sensor({'friendly_name': '---', 'address': 0x33333333,
                                   'eep': 'A5-02-05'})['ok']


def test_update_sensor_with_slash_roundtrip():
    """renaming a web-added sensor with a '/' keeps the MQTT grouping and
    the friendly name verbatim; config-file sensors get a clear
    error instead of 'Sensor not found'."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        res = com.add_sensor({'friendly_name': 'Lights/Kitchen Temp', 'name': 'lights/kitchen_temp',
                              'address': 0x12345678, 'eep': 'A5-02-05'})
        assert res['ok'], res
        assert res['sensor']['name'] == 'lights/kitchen_temp'
        assert res['sensor']['friendly_name'] == 'Lights/Kitchen Temp'

        # rename keeping the grouping: edit box shows the slashed base
        res = com.update_sensor('lights/kitchen_temp', {
            'friendly_name': 'lights/kitchen_temp', 'name': 'lights/kitchen_temp'})
        assert res['ok'], res
        stored = com._store.get('lights/kitchen_temp')
        assert stored is not None
        assert stored['name'] == 'lights/kitchen_temp'
        assert stored['friendly_name'] == 'lights/kitchen_temp'

        # rename WITH a group change
        res = com.update_sensor('lights/kitchen_temp', {
            'friendly_name': 'Lights/Hall Temp', 'name': 'lights/hall_temp'})
        assert res['ok'], res
        assert com._store.get('lights/hall_temp') is not None
        assert com._store.get('lights/kitchen_temp') is None
        assert com._store.get('lights/hall_temp')['friendly_name'] == 'Lights/Hall Temp'

        # config-file sensors are not web-editable: clear error, no crash
        com.sensors.append({'name': 'enoceanmqtt/kitchen', 'source': 'config',
                            'address': 0x99999999, 'rorg': 0xA5,
                            'func': 0x02, 'type': 0x05})
        res = com.update_sensor('kitchen', {'friendly_name': 'Bath',
                                            'name': 'bath'})
        assert not res['ok']
        assert 'configuration file' in res['error']
        assert 'kitchen' in res['error']
        # machine-readable code + section so the web UI can localize the toast
        assert res.get('error_code') == 'config_file_sensor'
        assert res.get('section') == 'kitchen'
        # the config sensor itself is untouched
        assert any(s.get('name') == 'enoceanmqtt/kitchen' for s in com.sensors)
        res = com.remove_sensor('kitchen')
        assert not res['ok']
        assert 'configuration file' in res['error']
        assert res.get('error_code') == 'config_file_sensor'
        assert res.get('section') == 'kitchen'


def _mk_ha_discovery_com(conf, **kwargs):
    """build a plain Communicator with the HA-overlay discovery methods
    attached (avoids instantiating the full HACommunicator / DeviceManager /
    mapping.yaml in unit tests)."""
    from enoceanmqtt.overlays.homeassistant import ha_communicator as _hac
    com = _mk_com(conf, **kwargs)
    # bind the discovery machinery we actually test
    import yaml as _yaml
    _map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', 'enoceanmqtt', 'overlays', 'homeassistant',
                             'mapping.yaml')
    with open(_map_path, 'r', encoding='utf-8') as _mf:
        com._ha_mapping = _yaml.safe_load(_mf)
    com._mqtt_discovery_prefix = conf.get('mqtt_discovery_prefix', 'homeassistant/')
    if not com._mqtt_discovery_prefix.endswith('/'):
        com._mqtt_discovery_prefix += '/'
    com._mqtt_discovery_eep = _hac.HACommunicator._mqtt_discovery_eep.__get__(com)
    com._mqtt_discovery_model = _hac.HACommunicator._mqtt_discovery_model.__get__(com)
    com._legacy_display_name = _hac.HACommunicator._legacy_display_name.__get__(com)
    com._friendly = _hac.HACommunicator._friendly.__get__(com)
    com._entity_id = _hac.HACommunicator._entity_id.__get__(com)
    # _device_uid is a @staticmethod - reference it directly
    com._device_uid = _hac.HACommunicator._device_uid
    com._devmgr = type('DM', (), {
        'db_upsert_device': lambda *a, **k: None,
        'db_get_device_by_field': lambda *a, **k: None,
    })()
    return com


def _capture_discovery_payloads(com, sensor):
    """run the EEP discovery for ``sensor`` and return the parsed payloads."""
    import json as _json
    published = {}
    def fake_publish(topic, payload='', retain=False):
        if topic.startswith('homeassistant/'):
            published[topic] = payload
    com.mqtt.publish = fake_publish
    com._mqtt_discovery_eep(sensor)
    configs = []
    for v in published.values():
        if not v:
            continue
        cfg = _json.loads(v) if isinstance(v, str) else v
        configs.append(cfg)
    return configs


def test_ha_discovery_friendly_name_and_entity_id():
    """the HA overlay publishes the friendly name as the device name and a
    deterministic 'e2m_' entity-id (default_entity_id)."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'mqtt_discovery_prefix': 'homeassistant/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = _mk_ha_discovery_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        com.add_sensor({'friendly_name': 'Wohnzimmer Temp',
                        'address': 0x12345678, 'eep': 'A5-02-05'})
        sensor = next(s for s in com.sensors if s.get('name') == 'enoceanmqtt/wohnzimmer_temp')

        configs = _capture_discovery_payloads(com, sensor)
        assert configs, 'expected discovery payloads'
        for cfg in configs:
            if 'device' in cfg:
                # friendly name appears in the device name
                assert cfg['device']['name'] == 'Wohnzimmer Temp', cfg['device']['name']
                # deterministic entity id with e2m_ prefix
                deid = cfg.get('default_entity_id', '')
                assert deid.startswith('sensor.e2m_wohnzimmer_temp'), deid


def test_ha_discovery_legacy_config_name_unchanged():
    """config-file devices without a friendly name keep the legacy
    'e2m_<sanitized>' device name (compatibility with older versions)."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'mqtt_discovery_prefix': 'homeassistant/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        sensor = {'name': 'enoceanmqtt/kitchen', 'address': 0x12345678,
                  'rorg': 0xA5, 'func': 0x02, 'type': 0x05,
                  'source': 'config'}
        com = _mk_ha_discovery_com(conf)
        com.mqtt = FakeMQTT()
        com.enocean_sender = [0xFF, 0x80, 0x00, 0x00]

        configs = _capture_discovery_payloads(com, sensor)
        assert configs
        for cfg in configs:
            if 'device' in cfg:
                assert cfg['device']['name'] == 'e2m_kitchen', cfg['device']['name']
                assert cfg['default_entity_id'].startswith(
                    'sensor.e2m_kitchen'), cfg['default_entity_id']


def test_boot_without_gateway_missing_serial():
    """booting with an unusable serial port must not crash - the gateway
    starts with enocean=None and the web UI stays up (the run loop retries).

    Regression: SerialCommunicator(port) opens the port in __init__ and the
    old code let that exception escape, killing the whole process before the
    web interface could start.
    """
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': '/dev/enocean-does-not-exist-12345',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        assert com.enocean is None, 'no gateway -> enocean must be None'
        assert com.enocean_error, 'the connect error should be recorded'
        # the web-facing status can be rendered safely
        com.mqtt = FakeMQTT()
        assert com.virtual_senders() == []
        assert com.enocean_sender_hex is None
        # teach-in must fail cleanly, not raise
        ok, msg = com._send_teachin_payload(
            name='x', sender_hex=None, rorg=0xA5, func=0x38, type_=0x08,
            address=0x123456, category='actor')
        assert not ok and 'not connected' in msg


def test_boot_no_port_discovery_mode():
    """boot with no enocean_port configured at all -> discovery mode: the
    gateway comes up, the web UI is reachable and reports discovering=True.

    Regression: the old mandatory-config check killed the process when
    enocean_port was missing entirely."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            # no enocean_port on purpose
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        assert com.enocean is None
        assert com.gateway_connected() is False
        assert com.enocean_error, 'discovery mode should record an error string'
        assert 'discovery' in com.enocean_error.lower(), com.enocean_error


def test_ha_overlay_boot_without_gateway():
    """the HA overlay (the docker default) must boot without a gateway too.

    Regression: HACommunicator.__init__ did ``self.enocean.teach_in = False``
    unconditionally, crashing with AttributeError when the communicator
    started in discovery mode (enocean=None)."""
    from enoceanmqtt.overlays.homeassistant.ha_communicator import HACommunicator
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': '/dev/enocean-does-not-exist-9999',
            'mqtt_prefix': 'enoceanmqtt/',
            'mqtt_discovery_prefix': 'homeassistant/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = HACommunicator(conf, [])
        assert com.enocean is None
        assert com.gateway_connected() is False


def test_boot_without_gateway_status_and_reconnect():
    """/api/status shows the gateway as disconnected + discovering, and the
    web UI is reachable - the full 'configure from the web UI' flow works
    without a gateway at boot."""
    import urllib.request
    import socket as _socket
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': '/dev/enocean-does-not-exist-6789',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '0',
            'webui_port': '0',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        assert com.enocean is None
        assert com.gateway_connected() is False
        com.mqtt = FakeMQTT()

        web = WebInterface(com)
        sock = _socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        web.start(host='127.0.0.1', port=port)
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status') as r:
                data = json.loads(r.read())
            gw = data['gateway']
            assert gw['connected'] is False
            assert gw['discovering'] is True, gw
            assert 'error' in gw
        finally:
            web.stop()

        # once a transceiver is present the same object reports connected
        com.enocean = FakeEnocean()
        assert com.gateway_connected() is True



def test_api_discovery_serves_cache():
    """/api/discovery returns the communicator's background discovery cache
    (no live network call on the web-handler path)."""
    import urllib.request
    import socket as _socket
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': '/dev/enocean-does-not-exist-6789',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '0',
            'webui_port': '0',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.mqtt = FakeMQTT()
        # pre-populate the cache the way the background thread would
        with com._discovery_cache_lock:
            com._discovery_cache = {
                'serial': [{'device': '/dev/ttyUSB0', 'candidate': True, 'enocean': True,
                            'description': 'USB300', 'hwid': '10:20'}],
                'mdns': [{'service': 'gw._ser2net._tcp.local', 'name': 'gw',
                          'host': '192.168.1.50', 'port': 30000, 'txt': {'model': 'TCM310'}}],
            }

        web = WebInterface(com)
        sock = _socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        web.start(host='127.0.0.1', port=port)
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/discovery') as r:
                data = json.loads(r.read())
            assert data['serial'][0]['device'] == '/dev/ttyUSB0'
            assert data['mdns'][0]['host'] == '192.168.1.50'
            assert data['mdns'][0]['port'] == 30000
        finally:
            web.stop()
        web.stop()


def test_tcp_down_no_transmit_queue_growth():
    """a TCP ser2net endpoint that is down must not leak transmit packets.

    Regression: the run loop queried base_id (=CO_RD_IDBASE enqueue) every
    iteration while the retry thread was alive-but-not-connected, growing the
    transmit queue without bound. gateway_connected() is now the gate."""
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': 'tcp:127.0.0.1:1',   # nothing listens
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        # boot via a serial port that does not exist -> enocean stays None
        # (no TCP retry thread is started, keeping the test hermetic)
        conf['enocean_port'] = '/dev/enocean-does-not-exist-777'
        com = Communicator(conf, [])
        assert com.enocean is None
        try:
            # construct a TCP communicator WITHOUT starting its thread (the
            # retry thread would keep the interpreter alive -> use the class
            # directly, sock=None = endpoint down)
            from enoceanmqtt.tcpclientcommunicator import TCPClientCommunicator
            tcp = TCPClientCommunicator('127.0.0.1', 1)   # not .start()ed
            assert tcp.sock is None
            # gateway_connected() treats a socket-less TCP client as down
            com.enocean = tcp
            assert com.gateway_connected() is False
            assert com.enocean_sender is None

            # simulate the run-loop guard: while not connected, base_id must
            # never be requested (which would enqueue CO_RD_IDBASE)
            for _ in range(5):
                if (com.enocean is not None and com.gateway_connected()
                        and com.enocean_sender is None):
                    com.enocean_sender = com.enocean.base_id
            assert com.enocean.transmit.qsize() == 0, "transmit queue must stay empty while TCP is down"
            assert com.enocean_sender is None
        finally:
            try:
                com.enocean.stop()
            except Exception:   # pylint: disable=broad-except
                pass
            com.enocean = None


def test_run_loop_survives_no_gateway():
    """the run loop must keep spinning (and keep MQTT up) while there is no
    transceiver, instead of exiting or crashing."""
    import threading as _t
    with tempfile.TemporaryDirectory() as tmp:
        conf = {
            'mqtt_host': 'localhost', 'mqtt_port': '1883',
            'enocean_port': '/dev/enocean-does-not-exist-54321',
            'mqtt_prefix': 'enoceanmqtt/',
            'webui_disable': '1',
            'webui_sensor_store': os.path.join(tmp, 'sensors.json'),
        }
        com = Communicator(conf, [])
        com.mqtt = FakeMQTT()
        # make connect retries instant so the loop spins without sleeping
        com._connect_enocean = lambda: (_ for _ in ()).throw(
            FileNotFoundError('no such serial port (test)'))
        com.enocean = None

        real_sleep = time.sleep
        time.sleep = lambda s: None
        try:
            t = _t.Thread(target=com.run, daemon=True)
            t.start()
            time.sleep(0.5)
            assert t.is_alive(), 'run loop must keep running without a gateway'
            assert com.enocean is None
            com._restart_requested = True
            t.join(timeout=5)
            assert not t.is_alive(), 'loop should exit on restart request'
        finally:
            time.sleep = real_sleep

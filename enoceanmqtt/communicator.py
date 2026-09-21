# Copyright (c) 2020 embyt GmbH. See LICENSE for further details.
# Author: Roman Morawek <roman.morawek@embyt.com>
"""this class handles the enocean and mqtt interfaces"""
import logging
import queue
import numbers
import json
import os
import re
import time
import threading
import datetime

from enocean.communicators.serialcommunicator import SerialCommunicator
from enoceanmqtt.tcpclientcommunicator import TCPClientCommunicator
from enocean.protocol.packet import Packet, RadioPacket, UTETeachInPacket
from enoceanmqtt.eep_engine import engine as eep_engine
from enoceanmqtt.eep_engine.utils import to_bitarray
from enoceanmqtt.cover import POSITION_SUBTOPIC, SHUT_TIME_SUBTOPIC, update_cover_position
from enoceanmqtt.cover_store import CoverStore
from enoceanmqtt.secure_store import SecureStore
from enoceanmqtt.thermokon_aliases import thermokon_alias_map
from enocean.protocol.constants import PACKET, RETURN_CODE, RORG
import enocean.utils
import paho.mqtt.client as mqtt

from enoceanmqtt.sensor_store import SensorStore
from enoceanmqtt.eep_registry import get_registry
from enoceanmqtt.diagnostics import (CO_RD_VERSION, CO_RD_REPEATER, CO_RD_DUTYCYCLE_LIMIT,
                                     EV_DUTYCYCLE_LIMIT, EV_TRANSMIT_FAILED,
                                     parse_version, parse_repeater, parse_duty_cycle)


#: accepted EnOcean address formats: int, '0x...', 'FF:80:00:00', 'A5-02-05'
_HEX_STRIP_RE = re.compile(r'[^0-9a-fA-F]')

#: minimum seconds between transceiver diagnostics COMMON_COMMAND rounds
DIAGNOSTICS_INTERVAL = 60


def parse_int(value):
    """parse an int that may be given as int, '0x...', 'FF:80:00:00' or plain hex"""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if not s:
        return None
    s = s.replace('0x', '').replace('0X', '')
    if ':' in s or '-' in s or ' ' in s:
        s = _HEX_STRIP_RE.sub('', s)
    try:
        return int(s, 16) if s else None
    except ValueError:
        return None


def parse_eep(eep):
    """parse an EEP string like 'A5-02-05' / '0xA5-0x02-0x05' into ints.

    Returns (rorg, func, type) or None when the string is not a valid 3-part EEP.
    """
    if not eep:
        return None
    parts = [p for p in eep.replace('0x', '').split('-') if p]
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p, 16) for p in parts)
    except ValueError:
        return None


def eep_dash(rorg, func=None, type_=None):
    """format an EEP as 'A5-02-05' (uppercase), or 'A5' when func/type are unknown"""
    return f'{rorg:02X}-{func:02X}-{type_:02X}' if func is not None and type_ is not None \
        else f'{rorg:02X}'


def sender_bytes(sender_int):
    """split a 32-bit int into the big-endian 4-byte list the enocean library uses"""
    return [(sender_int >> i * 8) & 0xff for i in reversed(range(4))]


#: legacy alias kept for backwards compatibility
_parse_int = parse_int


class Communicator:
    """the main working class providing the MQTT interface to the enocean packet classes"""
    mqtt = None
    enocean = None

    #: a sensor is considered "online" if it has been seen within this window
    ONLINE_TIMEOUT_SECONDS = 10 * 60
    #: MQTT CONNACK return-code descriptions (indexed by the code)
    CONNECTION_RETURN_CODE = {

    0: "connection successful",
        1: "incorrect protocol version",
        2: "invalid client identifier",
        3: "server unavailable",
        4: "bad username or password",
        5: "not authorised",
    }

    def __init__(self, config, sensors):
        self.conf = config
        self.sensors = sensors
        self._index_sensors()

        # UTE teach-in state (managed through the web interface / MQTT learn)
        self.learn_mode = False
        self._restart_requested = False
        # capture-only teach-in: fills the web dialog but does NOT persist
        self.learn_capture = False
        self.captured_device = None
        self._last_seen = {}
        # latest decoded values per device address (for the web UI)
        self._latest_value = {}
        # per-field unit/description metadata per device address
        self._field_meta = {}
        # rolling history of decoded values (for the value graph)
        self._history = {}
        self._history_max = int(self.conf.get('webui_history', 20))

        # EEP catalog + persistent store for web-added sensors
        self._eep_registry = get_registry()
        self._store = SensorStore(self._resolve_sensor_store_path())

        # transceiver diagnostics (chip id, repeater, duty-cycle, TX failures)
        self._diag = {
            'chip_id': None, 'app_version': None, 'api_version': None,
            'repeater_level': None, 'duty_cycle_available': None,
            'transmit_failures': 0,
        }
        self._dynamic_names = set()
        self._load_dynamic_sensors()

        # cached EEP catalog (built once, invalidated when sensors change so the
        # web UI's 2 s /api/status poll does not re-read + re-parse mapping.yaml)
        self._eep_catalog_cache = None

        # persistent cover positions (Eltako FSB-type, TinyDB)
        self._cover_store = CoverStore(self.conf.get('cover_positions_file'))
        # persistent rolling codes for secure (VAES) devices
        self._secure_store = SecureStore(self.conf.get('secure_rlc_file'))
        # per-device secure config: {address_hex: {key: <16-byte hex>, slf: <0x8B>, ...}}
        self._secure_config = {}
        try:
            raw = self.conf.get('secure_devices') or {}
            if isinstance(raw, str):
                import yaml
                raw = yaml.safe_load(raw) or {}
            for addr, cfg in (raw or {}).items():
                try:
                    self._secure_config[int(addr, 16)] = cfg
                except (ValueError, TypeError):
                    logging.warning("ignoring invalid secure device address: %s", addr)
        except Exception:   # pylint: disable=broad-except
            logging.exception("failed to parse secure_devices config")

        # check for mandatory configuration. mqtt_host is required (there is
        # nothing to do without a broker); enocean_port is NOT - without it
        # the gateway boots in discovery mode and stays up so a gateway can
        # be picked from the web UI.
        if 'mqtt_host' not in self.conf:
            raise Exception("Mandatory configuration not found: mqtt_host")
        mqtt_port = int(self.conf['mqtt_port']) if 'mqtt_port' in self.conf else 1883
        mqtt_keepalive = int(self.conf['mqtt_keepalive']) if 'mqtt_keepalive' in self.conf else 60

        # setup mqtt connection
        client_id = self.conf['mqtt_client_id'] if 'mqtt_client_id' in self.conf else ''
        self.mqtt = mqtt.Client(client_id=client_id)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect
        self.mqtt.on_message = self._on_mqtt_message
        if 'mqtt_user' in self.conf:
            logging.info("Authenticating: %s", self.conf['mqtt_user'])
            self.mqtt.username_pw_set(self.conf['mqtt_user'], self.conf['mqtt_pwd'])
        if str(self.conf.get('mqtt_ssl')) in ("True", "true", "1"):
            logging.info("Enabling SSL")
            ca_certs = self.conf['mqtt_ssl_ca_certs'] if 'mqtt_ssl_ca_certs' in self.conf else None
            certfile = self.conf['mqtt_ssl_certfile'] if 'mqtt_ssl_certfile' in self.conf else None
            keyfile = self.conf['mqtt_ssl_keyfile'] if 'mqtt_ssl_keyfile' in self.conf else None
            self.mqtt.tls_set(ca_certs=ca_certs, certfile=certfile, keyfile=keyfile)
            if str(self.conf.get('mqtt_ssl_insecure')) in ("True", "true", "1"):
                logging.warning("Disabling SSL certificate verification")
                self.mqtt.tls_insecure_set(True)
        if str(self.conf.get('mqtt_debug')) in ("True", "true", "1"):
            self.mqtt.enable_logger()
        logging.debug("Connecting to host %s, port %s, keepalive %s",
                      self.conf['mqtt_host'], mqtt_port, mqtt_keepalive)
        self.mqtt.connect_async(self.conf['mqtt_host'], port=mqtt_port, keepalive=mqtt_keepalive)
        self.mqtt.loop_start()

        # setup enocean communication. The transceiver is optional at boot:
        # when the configured port is missing (ser2net host not reachable yet,
        # dongle unplugged, or still scanning for one) the gateway keeps
        # running - and the web UI stays up - with self.enocean = None. The
        # run loop retries the connection in the background, and MQTT stays
        # connected the whole time.
        self.enocean = None
        self.enocean_sender = None
        self.enocean_error = None
        try:
            self._connect_enocean()
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("EnOcean gateway not available at boot: %s", exc)
            self.enocean_error = str(exc) or exc.__class__.__name__
            self.enocean = None
            self._start_gateway_discovery()

        # UTE teach-in telegrams are handled by this class, not by the library
        if self.enocean is not None:
            self.enocean.teach_in = False

        # start the embedded web interface
        self._webui = None
        if str(self.conf.get('webui_disable')) not in ("True", "true", "1"):
            try:
                from enoceanmqtt.webinterface import WebInterface
                self._webui = WebInterface(self)
                port = int(self.conf.get('webui_port', 8091))
                host = self.conf.get('webui_host', '0.0.0.0')
                self._webui.start(host, port)
            except Exception as exc:   # pylint: disable=broad-except
                logging.error("Cannot start web interface: %s", exc)

    def __del__(self):
        if self.enocean is not None and self.enocean.is_alive():
            self.enocean.stop()

    def _index_sensors(self):
        """index sensors by address and name for faster lookup"""
        self._sensors_by_address = {}
        self._sensors_by_name = {}
        self._sensor_to_index = {id(s): i for i, s in enumerate(self.sensors)}
        for sensor in self.sensors:
            addr = sensor.get('address')
            if addr is not None:
                if addr not in self._sensors_by_address:
                    self._sensors_by_address[addr] = []
                self._sensors_by_address[addr].append(sensor)

            name = sensor.get('name')
            if name:
                if name not in self._sensors_by_name:
                    self._sensors_by_name[name] = []
                self._sensors_by_name[name].append(sensor)

    def _get_sensors_for_topic(self, topic):
        """return list of sensors that match the given topic"""
        matched = []
        parts = topic.split('/')
        current = ""
        for i, part in enumerate(parts):
            if i > 0:
                current += "/"
            current += part
            if current in self._sensors_by_name and topic.startswith(current + "/"):
                matched.extend(self._sensors_by_name[current])
        # keep the configured sensor order
        if len(matched) > 1:
            matched.sort(key=lambda s: self._sensor_to_index.get(id(s), 0))
        return matched

    # ------------------------------------------------------------------
    # SENSOR STORE / WEB-ADDED SENSORS
    # ------------------------------------------------------------------
    def _resolve_sensor_store_path(self):
        """determine where sensors added through the web UI are persisted"""
        path = self.conf.get('webui_sensor_store')
        if path:
            return path
        # by default store next to the HA device database, if configured
        db_file = self.conf.get('db_file')
        if db_file:
            return os.path.join(os.path.dirname(os.path.abspath(db_file)), 'sensors.json')
        # otherwise next to the first configuration file
        config_files = self.conf.get('config') or []
        if config_files:
            return os.path.join(os.path.dirname(os.path.abspath(config_files[0])), 'sensors.json')
        return None

    def _prepare_dynamic_sensor(self, stored, full_name):
        """turn a stored sensor entry into a runnable sensor dict"""
        sensor = {
            'name': full_name,
            'address': stored['address'],
            'rorg': stored['rorg'],
            'func': stored.get('func'),
            'type': stored.get('type'),
            'source': 'dynamic',
            'persistent': '1',
        }
        if stored.get('sender'):
            sensor['sender'] = stored['sender']
        if stored.get('ignore'):
            sensor['ignore'] = stored['ignore']
        # the user-entered friendly name (free text, spaces allowed) is used
        # for the Home Assistant display name; 'name' stays the sanitized
        # MQTT-topic / entity-id base.
        if stored.get('friendly_name'):
            sensor['friendly_name'] = stored['friendly_name']
        for key in ('category', 'bidirectional', 'smartack', 'virtual',
                    'direction', 'answer', 'default_data', 'command', 'channel'):
            if stored.get(key) is not None:
                sensor[key] = stored[key]
        # resolve missing flags from the EEP registry so bidirectional /
        # smartACK behaviour works even for freshly taught-in devices
        if (stored.get('func') is not None and stored.get('type') is not None and
                sensor.get('bidirectional') is None):
            profile = self._eep_registry.get(sensor['rorg'], sensor['func'], sensor['type'])
            if profile:
                if sensor.get('category') is None:
                    sensor['category'] = profile.get('category', 'sensor')
                if sensor.get('bidirectional') is None:
                    sensor['bidirectional'] = bool(profile.get('bidirectional'))
                if sensor.get('smartack') is None:
                    sensor['smartack'] = bool(profile.get('smartack'))
        return sensor

    def _load_dynamic_sensors(self, target_name=None):
        """(re)merge sensors from the persistent store into the running config"""
        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        self.sensors = [s for s in self.sensors if s.get('source') != 'dynamic']
        self._dynamic_names = set()
        added = []
        for stored in self._store.all():
            full_name = prefix + stored['name']
            sensor = self._prepare_dynamic_sensor(stored, full_name)
            self.sensors.append(sensor)
            self._dynamic_names.add(full_name)
            if stored['name'] == target_name:
                added.append(sensor)
        self._index_sensors()
        self._invalidate_eep_catalog()
        return added[0] if added else None

    def add_sensor(self, payload):
        """add a sensor (manual or web interface) and persist it"""
        try:
            # The user enters a name that may contain spaces (the Home
            # Assistant friendly name) and '/' (MQTT topic grouping). The
            # stored 'name' is the sanitized MQTT-topic base (spaces -> '_',
            # '/' kept so the broker groups the sensor); 'friendly_name' is
            # the slash -> space display form shown in Home Assistant.
            friendly_raw = str(payload.get('friendly_name', '')).strip()
            name = str(payload.get('name', '')).strip()
            if not name or '/' in friendly_raw:
                name = self._sanitize_name(friendly_raw)
            address = payload.get('address')
            eep = str(payload.get('eep', '')).strip()
            sender = payload.get('sender')

            if not self._is_valid_name(name):
                return {'ok': False, 'error': 'A sensor name is required (letters, digits, _ - / only)'}
            # a name like '---' produces an empty topic base
            if not self._sanitize_name(friendly_raw or name):
                return {'ok': False, 'error': 'A device name is required (letters, digits, _ - /)'}
            friendly_name = self._friendly_from_input(friendly_raw or name)
            address = parse_int(address)
            if address is None:
                return {'ok': False, 'error': 'Invalid sensor address'}
            if not (0 <= address <= 0xFFFFFFFF):
                return {'ok': False, 'error': 'Sensor address out of range'}

            eep_parts = parse_eep(eep)
            if eep_parts is None:
                return {'ok': False, 'error': 'Invalid EEP, expected e.g. A5-02-05'}
            rorg, func, type_ = eep_parts
            if not self._is_valid_eep(rorg, func, type_):
                return {'ok': False, 'error': 'EEP is not in the known profile catalog'}

            prefix = self.conf.get('mqtt_prefix', 'enocean/')
            full_name = prefix + name
            if any(s.get('name') == full_name for s in self.sensors):
                return {'ok': False, 'error': 'A sensor with this name already exists'}

            stored = {'name': name, 'friendly_name': friendly_name,
                      'address': address,
                      'rorg': rorg, 'func': func, 'type': type_}
            if sender:
                sp = parse_int(sender)
                if sp is None:
                    return {'ok': False, 'error': 'Invalid sender address'}
                stored['sender'] = sp
            # optional per-sensor overrides (e.g. mark an EEP as bidirectional,
            # or actor / bidirectional settings like direction, answer, default_data)
            for key in ('category', 'bidirectional', 'smartack', 'virtual',
                        'direction', 'answer', 'default_data', 'command', 'channel'):
                if payload.get(key) is not None:
                    stored[key] = payload[key]
            self._store.add(stored)
            new_sensor = self._load_dynamic_sensors(name)
            self._on_sensors_changed(new_sensor)
            return {'ok': True, 'sensor': self.describe_sensor(new_sensor)}
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("add_sensor failed: %s", exc)
            return {'ok': False, 'error': str(exc)}

    def remove_sensor(self, name):
        """remove a web-added sensor and clean up (HA discovery, etc.)"""
        name = str(name).strip()
        stored = self._store.get(name)
        if not stored:
            config_match = self._find_config_sensor(name)
            if config_match is not None:
                section = self._display_name(config_match)
                return {'ok': False,
                        'error_code': 'config_file_sensor',
                        'section': section,
                        'error': (
                            "This device is defined in the configuration file "
                            "(section [%s]). It cannot be removed from the web UI - "
                            "remove the section and restart the gateway." % section)}
            return {'ok': False, 'error': 'Sensor not found'}
        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        full = next((s for s in self.sensors if s.get('name') == prefix + name), None)
        if self._store.remove(name):
            self._load_dynamic_sensors()
            if full is not None:
                self._on_sensor_removed(full)
            return {'ok': True}
        return {'ok': False, 'error': 'Could not remove sensor'}

    def update_sensor(self, name, payload):
        """update a web-added sensor (rename and/or change the EEP)."""
        name = str(name).strip()
        stored = self._store.get(name)
        if not stored:
            config_match = self._find_config_sensor(name)
            if config_match is not None:
                section = self._display_name(config_match)
                return {'ok': False,
                        'error_code': 'config_file_sensor',
                        'section': section,
                        'error': (
                            "This device is defined in the configuration file "
                            "(section [%s]). It cannot be modified from the web UI - "
                            "edit the section and restart the gateway." % section)}
            return {'ok': False, 'error': 'Sensor not found'}

        changes = {}
        # optional rename: the user edits the name (spaces and '/' allowed
        # for MQTT topic grouping). The stored 'name' (MQTT topic base) keeps
        # the slashes; 'friendly_name' (Home Assistant display) maps '/' to a
        # space and is stored in slash -> space form.
        new_friendly = payload.get('friendly_name')
        new_name = payload.get('name')
        if new_friendly is not None:
            new_friendly = str(new_friendly).strip()
            if not new_friendly:
                return {'ok': False, 'error': 'A device name is required'}
            new_topic = self._sanitize_name(new_friendly)
            if not new_topic:
                return {'ok': False, 'error': 'A device name is required (letters, digits, _ - /)'}
            changes['friendly_name'] = self._friendly_from_input(new_friendly)
            if new_name in (None, ''):
                new_name = new_topic
        if new_name is not None:
            new_name = str(new_name).strip()
            if not self._is_valid_name(new_name):
                return {'ok': False, 'error': 'A sensor name is required (letters, digits, _ - / only)'}
            prefix = self.conf.get('mqtt_prefix', 'enocean/')
            if new_name != name and any(
                    s.get('name') == prefix + new_name for s in self.sensors):
                return {'ok': False, 'error': 'A sensor with this name already exists'}
            changes['name'] = new_name

        # optional address change
        address = payload.get('address')
        if address is not None:
            address = parse_int(address)
            if address is None:
                return {'ok': False, 'error': 'Invalid sensor address'}
            if not (0 <= address <= 0xFFFFFFFF):
                return {'ok': False, 'error': 'Sensor address out of range'}
            changes['address'] = address

        # optional EEP change
        eep = payload.get('eep')
        if eep is not None:
            eep_parts = parse_eep(str(eep).strip())
            if eep_parts is None:
                return {'ok': False, 'error': 'Invalid EEP, expected e.g. A5-08-01'}
            changes['rorg'], changes['func'], changes['type'] = eep_parts
            if not self._is_valid_eep(changes['rorg'], changes['func'], changes['type']):
                return {'ok': False, 'error': 'EEP is not in the known profile catalog'}

        if not changes:
            return {'ok': False, 'error': 'Nothing to update'}

        updated = self._store.update(name, changes)
        if updated is None:
            return {'ok': False, 'error': 'Could not update sensor'}

        # reload the merged sensors (handles rename + EEP change + HA discovery)
        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        old_full = prefix + name
        old_sensor = next((s for s in self.sensors if s.get('name') == old_full), None)
        self._load_dynamic_sensors()
        new_full = prefix + (changes.get('name') or name)
        new_sensor = next((s for s in self.sensors if s.get('name') == new_full), None)

        # HA overlay: remove discovery of the old name, publish for the new
        if old_sensor is not None and (changes.get('name') or old_full != new_full):
            self._on_sensor_removed(old_sensor)
        if new_sensor is not None:
            self._on_sensors_changed(new_sensor)
        return {'ok': True, 'sensor': self.describe_sensor(new_sensor) if new_sensor else None}

    def _on_sensors_changed(self, sensor=None):
        """hook called after sensors are added/changed (overridden by overlays)"""
        logging.debug("Sensors changed: %s", sensor.get('name') if sensor else 'all')

    def _on_sensor_removed(self, sensor):
        """hook called after a sensor is removed (overridden by overlays)"""
        logging.debug("Sensor removed: %s", sensor.get('name'))

    def get_history(self, name):
        """rolling value history for a device, for the web UI graph"""
        # resolve the device name (with or without prefix) to an address
        full = name
        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        if not full.startswith(prefix):
            full = prefix + name
        sensor = next((s for s in self.sensors if s.get('name') == full), None)
        if sensor is None:
            return {'ok': False, 'error': 'Device not found'}
        address = sensor.get('address')
        hist = self._history.get(address, [])
        return {'ok': True, 'name': name, 'history': hist[-self._history_max:]}

    def next_free_sender(self):
        """the first unused virtual sender ID (base+1..base+127), or None"""
        used = set()
        for s in self.sensors:
            if s.get('sender') is not None:
                used.add(s['sender'])
        for v in self.virtual_senders():
            if v not in used:
                return v
        return None

    def save_config(self, payload):
        """persist updated [CONFIG] settings back to the configuration file.

        ``payload`` is a dict of key/value pairs (plain strings). The first
        configuration file from ``self.conf['config']`` is rewritten; changes
        take effect after a restart.
        Returns {'ok': True} or {'ok': False, 'error': ...}.
        """
        try:
            config_files = self.conf.get('config') or []
            if not config_files:
                return {'ok': False, 'error': 'No configuration file configured'}
            conf_file = config_files[0]
            if not os.path.isfile(conf_file):
                return {'ok': False, 'error': 'Configuration file not found: %s' % conf_file}

            from configparser import ConfigParser
            parser = ConfigParser(inline_comment_prefixes=('#', ';'), interpolation=None)
            parser.read(conf_file)
            if not parser.has_section('CONFIG'):
                parser.add_section('CONFIG')
            for key in payload:
                if key in ('config',):
                    continue
                parser.set('CONFIG', key, str(payload[key]))
            with open(conf_file, 'w', encoding='utf-8') as f:
                parser.write(f)
            # update the in-memory conf so the running gateway sees the values
            for key, value in payload.items():
                self.conf[key] = str(value)
            logging.info("Updated [CONFIG] in %s", conf_file)
            # apply the changed settings live - no process restart needed
            self._apply_live_config(payload)
            return {'ok': True}
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("save_config failed: %s", exc)
            return {'ok': False, 'error': str(exc)}

    def _apply_live_config(self, payload):
        """apply config changes without restarting the process.

        Reconnects the MQTT client when broker settings changed and the
        EnOcean transceiver when the port changed. The web UI stays up so a
        misconfigured broker/port does not take the configurator down.
        """
        changed = set(payload.keys())
        mqtt_keys = {'mqtt_host', 'mqtt_port', 'mqtt_user', 'mqtt_pwd',
                     'mqtt_ssl', 'mqtt_ssl_insecure', 'mqtt_ssl_ca_certs',
                     'mqtt_ssl_certfile', 'mqtt_ssl_keyfile',
                     'mqtt_keepalive', 'mqtt_client_id', 'mqtt_prefix'}
        if changed & mqtt_keys:
            logging.info("MQTT settings changed - reconnecting live")
            try:
                self._reconnect_mqtt()
            except Exception as exc:   # pylint: disable=broad-except
                logging.error("live MQTT reconnect failed: %s", exc)
        if 'enocean_port' in changed:
            logging.info("EnOcean port changed - reconnecting transceiver live")
            try:
                self._reconnect_enocean()
            except Exception as exc:   # pylint: disable=broad-except
                logging.error("live EnOcean reconnect failed: %s", exc)

    def _reconnect_mqtt(self):
        """reconnect the MQTT client with the current conf values."""
        if not self.mqtt:
            return
        try:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
        except Exception:   # pylint: disable=broad-except
            pass
        mqtt_port = int(self.conf.get('mqtt_port', 1883))
        keepalive = int(self.conf.get('mqtt_keepalive', 60))
        client_id = self.conf.get('mqtt_client_id', '')
        self.mqtt = mqtt.Client(client_id=client_id)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect
        self.mqtt.on_message = self._on_mqtt_message
        if self.conf.get('mqtt_user'):
            self.mqtt.username_pw_set(self.conf['mqtt_user'], self.conf.get('mqtt_pwd'))
        if str(self.conf.get('mqtt_ssl')) in ("True", "true", "1"):
            self.mqtt.tls_set(
                ca_certs=self.conf.get('mqtt_ssl_ca_certs'),
                certfile=self.conf.get('mqtt_ssl_certfile'),
                keyfile=self.conf.get('mqtt_ssl_keyfile'))
            if str(self.conf.get('mqtt_ssl_insecure')) in ("True", "true", "1"):
                self.mqtt.tls_insecure_set(True)
        self.mqtt.connect_async(self.conf['mqtt_host'], port=mqtt_port, keepalive=keepalive)
        self.mqtt.loop_start()

    def _connect_enocean(self):
        """build + start the transceiver communicator for the current port.

        Returns the live communicator. Raises (KeyError/ValueError/serial
        errors) when the configured port is unusable - the caller decides
        whether that is fatal or (boot/reconnect) just deferred.
        """
        eport = (self.conf.get('enocean_port') or '').strip()
        if not eport:
            # no port configured - the gateway runs in discovery mode and the
            # web UI offers the discovered serial/mDNS endpoints
            raise ValueError('no EnOcean port configured - gateway discovery active')
        seport = eport.split(':')
        if seport[0] == "tcp":
            logging.info("connecting TCPClient to %s port %d", seport[1], int(seport[2]))
            self.enocean = TCPClientCommunicator(seport[1], int(seport[2]))
        else:
            logging.info("connecting Serial to %s", eport)
            self.enocean = SerialCommunicator(eport)
        self.enocean.start()
        # sender will be automatically determined once the transceiver answers
        self.enocean_sender = None
        self.enocean_error = None
        return self.enocean

    def _reconnect_enocean(self):
        """(re)connect the EnOcean transceiver with the current conf port."""
        if getattr(self, 'enocean', None) is not None:
            try:
                self.enocean.stop()
            except Exception:   # pylint: disable=broad-except
                pass
            self.enocean = None
        self._connect_enocean()
        if self.enocean is not None:
            self.enocean.teach_in = False

    def _start_gateway_discovery(self):
        """kick off a background gateway hunt (serial + mDNS) when the
        configured port is not reachable at boot."""
        try:
            from enoceanmqtt.device_discovery import discover
            threading.Thread(target=discover, kwargs={'timeout': 2.0},
                             daemon=True, name='gw-discovery').start()
        except Exception:   # pylint: disable=broad-except
            pass

    def _classify_category(self, sensor):
        """the device category the web UI shows (mirrors describe_sensor).

        - bidirectional devices (stored flag or resolved from the EEP
          registry) are always 'bidirectional'
        - virtual one-way actors (sender set + virtual/0xFFFFFFFF address) are
          'actor'
        - everything else is 'sensor'
        """
        address = sensor.get('address')
        is_virtual = bool(sensor.get('virtual')) or address == 0xFFFFFFFF
        # resolve the bidirectional flag exactly like describe_sensor does
        bidirectional = sensor.get('bidirectional')
        if bidirectional is None and sensor.get('func') is not None and sensor.get('type') is not None:
            profile = self._eep_registry.get(sensor.get('rorg'), sensor.get('func'), sensor.get('type'))
            if profile:
                bidirectional = bool(profile.get('bidirectional'))
        if bidirectional:
            return 'bidirectional'
        if sensor.get('sender') is not None and is_virtual:
            return 'actor'
        return 'sensor'

    @staticmethod
    def _sanitize_name(name):
        """turn a user-entered device name into the stored MQTT topic base.

        Lowercase; '/' is KEPT so it groups the sensor in the MQTT broker
        (``enoceanmqtt/lights/kitchen/...``); spaces and any other character
        outside [a-z0-9_/] become '_'. Runs of '_' collapse, '_' hugging a
        '/' is dropped (so 'a / b' -> 'a/b'), edges trimmed.

        "Lights/Kitchen Temp" -> "lights/kitchen_temp"
        "Wohnzimmer Temp"    -> "wohnzimmer_temp"
        "Living Room / Temp" -> "living_room/temp"
        """
        n = str(name or '').strip().lower()
        n = re.sub(r'[^a-z0-9_/]+', '_', n)
        n = re.sub(r'_+', '_', n)
        n = re.sub(r'_/', '/', n).replace('/_', '/')
        n = re.sub(r'/{2,}', '/', n)
        return n.strip('_/')

    @staticmethod
    def _friendly_from_input(name):
        """the Home Assistant friendly name for a user-entered name.

        The typed name is preserved verbatim (slashes and spaces alike) -
        it is what the user sees in Home Assistant and what the edit dialog
        round-trips. Leading/trailing whitespace is trimmed and runs of
        whitespace collapse, but '/' is never rewritten.
        "test/test test" -> "test/test test"
        "Living Room / Temp" -> "Living Room / Temp"
        """
        return re.sub(r' +', ' ', str(name or '').strip()).strip()

    @staticmethod
    def _sanitize_entity_id(name):
        """turn a friendly name into a Home Assistant entity-ID slug.

        Lowercase, and any character outside [a-z0-9_] (spaces, '-', '/',
        umlauts, ...) becomes '_' - Home Assistant entity_ids only allow
        [a-z0-9_] (homeassistant/core.VALID_ENTITY_ID). Runs of '_' collapse,
        edges trimmed. Used wherever a pure entity-ID slug is needed (the
        stored MQTT topic base keeps '/' via ``_sanitize_name``).

        "Lights Kitchen Temp" -> "lights_kitchen_temp"
        "Temp-Garage"         -> "temp_garage".
        """
        n = str(name or '').strip().lower()
        n = re.sub(r'[^a-z0-9_]+', '_', n)
        n = re.sub(r'_+', '_', n).strip('_')
        return n

    @staticmethod
    def _is_valid_name(name):
        """true for names containing only letters, digits, '_', '-' and '/'.

        This is the *entity-id / MQTT topic* form of the name (spaces are
        handled separately via ``friendly_name``): '/' groups devices and
        becomes '_' in the Home Assistant device name.
        """
        n = str(name or '').strip()
        return bool(n) and re.fullmatch(r'[A-Za-z0-9_\-\/]+', n) is not None

    def _is_valid_eep(self, rorg, func, type_):
        """True when the (rorg, func, type) triplet is a known EEP profile.

        Only known equipment profiles may be added/edited - a free-form
        'A5-99-99' is not a valid EnOcean profile and must be rejected.
        """
        if type_ is None or func is None:
            return False
        return self._eep_registry.get(rorg, func, type_) is not None

    def _display_name(self, sensor):
        """the name shown in the web UI / used by row buttons.

        Mirrors the exact transformation the frontend sees so lookups by
        display name (e.g. teach-in) can round-trip correctly:
        - mqtt_prefix is stripped
        - model-based sensors hide an internal trailing "/XX" rorg suffix
        """
        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        display_name = sensor['name']
        if display_name.startswith(prefix):
            display_name = display_name[len(prefix):]
        if sensor.get('model') and len(display_name) > 3 and display_name[-3] == '/':
            display_name = display_name[:-3]
        return display_name

    def _find_config_sensor(self, name):
        """find a configuration-file sensor by its display name (prefix
        stripped, model '/XX' suffix hidden), or None."""
        for sensor in self.sensors:
            if sensor.get('source') == 'dynamic':
                continue
            if self._display_name(sensor) == name:
                return sensor
        return None

    def describe_sensor(self, sensor):
        """build a JSON-friendly status description of a device"""
        address = sensor.get('address')
        last_seen = self._last_seen.get(address)
        status = 'never'
        if last_seen is not None:
            age = (datetime.datetime.utcnow() - last_seen).total_seconds()
            status = 'online' if age <= self.ONLINE_TIMEOUT_SECONDS else 'offline'

        rorg = sensor.get('rorg')
        func = sensor.get('func')
        type_ = sensor.get('type')
        eep = None
        eep_name = None
        rorg_name = None
        category = 'sensor'
        bidirectional = False
        smartack = False
        profile = None
        if rorg is not None:
            eep = eep_dash(rorg, func, type_)
            profile = self._eep_registry.get(rorg, func, type_)
            if profile:
                eep_name = profile['name']
                rorg_name = profile['rorg_name']
                category = profile.get('category', 'sensor')
                bidirectional = bool(profile.get('bidirectional'))
                smartack = bool(profile.get('smartack'))

        # explicit per-sensor override from the configuration file
        category = sensor.get('category', category)
        bidirectional = sensor.get('bidirectional', bidirectional)
        smartack = sensor.get('smartack', smartack)

        # Categorise by data model:
        #  - A BIDIRECTIONAL device (can both send and receive, e.g. A5-20-xx,
        #    D2-11 smartACK, D2-01, D2-06) is its own third category - it is
        #    ALWAYS bidirectional, regardless of whether a sender is set.
        #  - An ACTOR is a virtual device (virtual=1 AND a sender ID, address
        #    usually 0xFFFFFFFF) whose EEP is a one-way receiver.
        #  - Everything else is a sensor (has a device address).
        is_virtual = bool(sensor.get('virtual')) or address == 0xFFFFFFFF
        if bidirectional:
            category = 'bidirectional'
        elif sensor.get('sender') is not None and is_virtual:
            category = 'actor'
        else:
            category = 'sensor'

        # mark UTC timestamps so the browser renders them in the local timezone
        last_seen_iso = None
        if last_seen is not None:
            last_seen_iso = last_seen.isoformat()
            if not last_seen_iso.endswith('Z') and '+' not in last_seen_iso:
                last_seen_iso += 'Z'

        display_name = self._display_name(sensor)

        latest = self._latest_value.get(address)
        field_meta = self._field_meta.get(address, {})
        if latest is not None:
            latest = dict(latest)
            latest['meta'] = field_meta

        return {
            'name': display_name,
            # the friendly name is the raw user-entered name (slashes kept)
            # so it round-trips losslessly through the edit dialog.
            'friendly_name': sensor.get('friendly_name') or display_name,
            'address': address,
            'sender': sensor.get('sender'),
            'virtual': sensor.get('virtual'),
            'latest': latest,
            'eep': eep,
            'eep_name': eep_name,
            'rorg_name': rorg_name,
            'status': status,
            'last_seen': last_seen_iso,
            'source': 'dynamic' if sensor.get('source') == 'dynamic' else 'config',
            'model': sensor.get('model'),
            'category': category,
            'bidirectional': bidirectional,
            'smartack': smartack,
        }

    @staticmethod
    def _fmt_eep(eep):
        """format an EEP id as 'A5-20-01' (uppercase, no 0x prefix)"""
        return '-'.join(x.replace('0x', '').upper() if x.lower().startswith('0x') else x.upper()
                        for x in eep.split('-'))

    def eep_catalog(self):
        """return the list of known EnOcean equipment profiles.

        The standard code-defined catalog plus the Eltako models that add
        functionality beyond the standard EEP:
          * shutter / blind actuators (FSB14, FSB61, FJ62, TF61J) - these have
            cover-position tracking on top of the plain A5-3F-7F / F6-02-01.
        Every other Eltako model uses a plain standard EEP, so it is attached
        as a search-only **alias** to the matching standard profile (typing
        e.g. 'FSR14' finds it) but is NOT shown as a duplicate dropdown entry.
        """
        # The catalog is static at runtime: build + cache it once. Rebuilding
        # on every /api/status poll (2 s) costs a full mapping.yaml read +
        # parse + alias merge, which is pure waste and shows up as constant
        # CPU. Invalidated via _invalidate_eep_catalog() when sensors change.
        if self._eep_catalog_cache is not None:
            return self._eep_catalog_cache
        # standard catalog
        catalog = [{'eep': self._fmt_eep(p['eep']), 'name': p['name'],
                    'rorg_name': p['rorg_name'],
                    'category': p.get('category', 'sensor'),
                    'bidirectional': bool(p.get('bidirectional')),
                    'smartack': bool(p.get('smartack'))}
                   for p in self._eep_registry.profiles]
        by_eep = {e['eep']: e for e in catalog}
        special, plain = self._eltako_models()
        # 1) visible Eltako entries for the models with extra functionality
        for eep in sorted(special):
            models = special[eep]
            catalog.append({
                'eep': eep,
                'name': 'Eltako ' + ', '.join(sorted(models)),
                'rorg_name': 'Eltako',
                'category': 'sensor',
                'bidirectional': False,
                'smartack': False,
                'eltako_model': True,
            })
        # 2) search-only aliases on the matching standard profile
        for eep, models in plain.items():
            entry = by_eep.get(eep)
            if entry is None:
                continue
            aliases = sorted(set(entry.get('aliases', []) + models))
            entry['aliases'] = aliases  # not appended to name -> not shown in dropdown
        # 3) Thermokon search aliases (Thermokon <type>; SR65+ successor added)
        for eep, names in thermokon_alias_map().items():
            entry = by_eep.get(eep)
            if entry is None:
                continue
            entry['aliases'] = sorted(set(entry.get('aliases', [])) | set(names))
        self._eep_catalog_cache = catalog
        return catalog

    def _invalidate_eep_catalog(self):
        """drop the cached EEP catalog after sensors change.

        The catalog itself only depends on mapping.yaml + the EEP registry,
        both static at runtime - but invalidating on sensor changes is cheap
        and guarantees the /api/status payload (which embeds the catalog) can
        never go stale if that ever changes.
        """
        self._eep_catalog_cache = None

    def _eltako_models(self):
        """split the Eltako models into (special, plain).

        ``special`` = models whose mapping adds functionality the plain
        standard EEP does not have (shutter/cover position) -> shown as a
        selectable 'Eltako <model>' entry.
        ``plain``   = all other models -> search-only aliases on the standard
        EEP (found when typing, hidden from the dropdown).

        Returns (special, plain): both dicts {eep: [model, ...]}.
        """
        try:
            import yaml
            mapping_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        'overlays', 'homeassistant', 'mapping.yaml')
            if not os.path.isfile(mapping_file):
                return {}, {}
            data = yaml.safe_load(open(mapping_file, encoding='utf-8')) or {}
            eltako = data.get('eltako') or {}
            special = {}
            plain = {}
            for model, model_cfg in eltako.items():
                if not isinstance(model_cfg, dict):
                    continue
                # does this model add a cover (shutter position) entity?
                comps = {e.get('component') for e in (model_cfg.get('entities') or [])
                         if isinstance(e, dict)}
                is_special = 'cover' in comps
                for dc in (model_cfg.get('device_config') or []):
                    if not isinstance(dc, dict):
                        continue
                    rorg = dc.get('rorg'); func = dc.get('func'); type_ = dc.get('type')
                    if not (rorg and func and type_):
                        continue
                    try:
                        eep = '-'.join('%02X' % int(v, 16) for v in (rorg, func, type_))
                    except (ValueError, TypeError):
                        continue
                    bucket = special if is_special else plain
                    bucket.setdefault(eep, set()).add(model.upper())
            return {k: sorted(v) for k, v in special.items()}, {k: sorted(v) for k, v in plain.items()}
        except Exception:   # pylint: disable=broad-except
            logging.exception("failed to load Eltako models from mapping.yaml")
            return {}, {}


    def _eltako_aliases(self):
        """map each Eltako model's standard EEP -> list of model names.

        Returns {eep: [model, ...]} from the HA mapping's eltako section.
        Eltako uses standard EnOcean EEPs, so they become search aliases on
        the existing standard profile instead of duplicate dropdown entries.
        """
        try:
            import yaml
            mapping_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        'overlays', 'homeassistant', 'mapping.yaml')
            if not os.path.isfile(mapping_file):
                return {}
            data = yaml.safe_load(open(mapping_file, encoding='utf-8')) or {}
            eltako = data.get('eltako') or {}
            out = {}
            for model, model_cfg in eltako.items():
                if not isinstance(model_cfg, dict):
                    continue
                for dc in (model_cfg.get('device_config') or []):
                    if not isinstance(dc, dict):
                        continue
                    rorg = dc.get('rorg'); func = dc.get('func'); type_ = dc.get('type')
                    if not (rorg and func and type_):
                        continue
                    parts = []
                    for v in (rorg, func, type_):
                        try:
                            parts.append('%02X' % int(v, 16))
                        except (ValueError, TypeError):
                            parts = None
                            break
                    if not parts:
                        continue
                    out.setdefault('-'.join(parts), set()).add(model.upper())
            return {k: list(v) for k, v in out.items()}
        except Exception:   # pylint: disable=broad-except
            logging.exception("failed to load Eltako aliases from mapping.yaml")
            return {}



    @property
    def diagnostics(self):
        """transceiver diagnostics (chip id, repeater, duty-cycle, TX fails).

        Internal rate-limiting bookkeeping (``_last_query`` / ``_query_answered``)
        is filtered out so it never leaks to MQTT or the web UI.
        """
        return {k: v for k, v in self._diag.items() if not k.startswith('_')}

    @property
    def enocean_sender_hex(self):
        if self.enocean_sender is None:
            return None
        return enocean.utils.to_hex_string(self.enocean_sender)

    def gateway_connected(self):
        """is the EnOcean transceiver actually usable right now?

        The retry threads (TCPClient/SerialCommunicator) stay alive while the
        dongle is gone, so thread-alive alone is NOT "connected". A transceiver
        is connected only when data can truly flow:

          * TCPClientCommunicator  - a live socket exists (``sock`` attr)
          * SerialCommunicator     - the serial port is open
          * plain threads (tests)  - fall back to thread-alive

        Without this a TCP ser2net host that is down would still report
        "connected" in the web UI (the retry thread keeps running by design).
        """
        en = getattr(self, 'enocean', None)
        if en is None:
            return False
        # TCP client: a live socket means data can flow; a missing socket
        # means the retry thread is still hunting for the endpoint.
        try:
            sock = en.sock
            return sock is not None
        except AttributeError:
            pass
        # serial client: report connected only while the port is open
        try:
            ser = en._SerialCommunicator__ser
            return bool(ser is not None and getattr(ser, 'is_open', False))
        except AttributeError:
            pass
        # plain communicator/test double: thread aliveness is the best signal
        return bool(en.is_alive())

    def virtual_senders(self):
        """the usable virtual sender IDs of the transceiver.

        EnOcean transceivers (USB300/TCM515 and similar) expose a 32-bit base
        ID plus a range of 127 additional assignable addresses. The base ID
        itself is reserved (it is the transceiver's own ID), so the usable
        sender IDs are base+1 .. base+127 (127 IDs). These are used as the
        sender ID when transmitting to actors.
        Returns a list of ints (empty until the base ID is known).
        """
        base = self.enocean_sender or getattr(self.enocean, 'base_id', None)
        if base is None:
            return []
        base_int = enocean.utils.combine_hex(base)
        # base+1 .. base+127 (the base ID itself is not usable as a sender)
        return [base_int + i for i in range(1, 128)]

    # ------------------------------------------------------------------
    # UNIVERSAL TEACH-IN (UTE)
    # ------------------------------------------------------------------
    def set_learn_mode(self, enabled):
        """enable or disable UTE teach-in mode (one-shot)"""
        self.learn_mode = bool(enabled)
        # UTE responses are handled by this class, keep the library from auto-answering
        if self.enocean is not None:
            self.enocean.teach_in = False
        logging.info("UTE teach-in mode %s", "enabled" if self.learn_mode else "disabled")

    def start_capture(self):
        """start capture-only teach-in: fills the web dialog but does NOT add
        the device to the configuration."""
        self.learn_capture = True
        self.learn_mode = True
        self.captured_device = None
        logging.info("Teach-in capture mode enabled (dialog only)")

    def stop_capture(self):
        self.learn_capture = False
        self.learn_mode = False

    def get_captured(self):
        """return the captured device (and clear it), or None"""
        dev = self.captured_device
        self.captured_device = None
        return dev

    def _learn_unknown_device(self, packet, rorg=None, func=None, type_=None):
        """teach-in an unknown device that sent a telegram while learn mode
        is active.

        Works for devices that do not use UTE: regular 4BS/VLD sensors and RPS
        (F6) rocker switches have no teach-in button, they just send normal
        telegrams. If the EEP could be determined from the telegram it is
        stored, otherwise the device is registered with just its address and
        RORG so it can be matched and refined in the web interface.
        """
        address = enocean.utils.combine_hex(packet.sender)
        address_hex = enocean.utils.to_hex_string(packet.sender)

        if self._sensors_by_address.get(address):
            # already known - nothing to teach in
            return None

        if rorg is None:
            rorg = packet.rorg
        name = 'learn_' + format(address, '08x')
        stored = {'name': name, 'friendly_name': name,
                  'address': address, 'rorg': rorg}
        if func is not None and type_ is not None:
            # EEP was extracted from the telegram (e.g. a 4BS learn telegram)
            stored['func'] = func
            stored['type'] = type_
        else:
            # Devices like RPS/F6 rocker switches and 1BS contacts do not
            # carry an EEP in their telegram - they are identified by their
            # ID alone. For RPS/F6 we try to recognize the exact EEP from the
            # telegram's data byte (smoke, leakage, key card, rocker, window
            # handle, push button); otherwise a sensible default profile for
            # the RORG is used.
            recognized = self._recognize_rps_eep(packet) if rorg == RORG.RPS else None
            if recognized:
                stored['func'] = recognized[0]
                stored['type'] = recognized[1]
            else:
                default = self._eep_registry.default_for_rorg(rorg)
                if default:
                    stored['func'] = default['func']
                    stored['type'] = default['type']
        eep = eep_dash(rorg, stored.get('func'), stored.get('type'))
        if self.learn_capture:
            # capture-only: expose the detected device for the web dialog to
            # pre-fill, but do NOT add it to the configuration yet (the user
            # confirms, especially the name, before saving).
            self.captured_device = {
                'name': name, 'address': address, 'rorg': rorg,
                'func': stored.get('func'), 'type': stored.get('type'),
                'eep': eep,
            }
            self.learn_capture = False
            self.learn_mode = False
            return None
        self._store.add(stored)
        new_sensor = self._load_dynamic_sensors(name)
        if new_sensor is not None:
            self._on_sensors_changed(new_sensor)
        logging.info("Teach-in: captured device %s (RORG %s, EEP %s)",
                     address_hex, hex(rorg), eep)

        # teach-in is one-shot: disable it after a device was captured
        self.learn_mode = False
        return new_sensor

    @staticmethod
    def _is_4bs_learn_telegram(packet):
        """a 4BS teach-in telegram.

        Uses the enocean library's own parsing: for 4BS, ``packet.learn`` is
        set from the (active-low) LRN bit in DB0 and ``rorg_func``/``rorg_type``
        are decoded from the documented EEP bit layout. A 4BS telegram is a
        teach-in iff the library flags it as a learn telegram and the EEP was
        detected.
        """
        if packet.rorg != RORG.BS4 or len(packet.data) < 5:
            return False
        return bool(packet.learn and packet.rorg_func is not None and
                    packet.rorg_type is not None)

    def _handle_4bs_learn_telegram(self, packet):
        """register a device from a 4BS learn telegram.

        The EEP (FUNC/TYPE) is taken from the enocean library's parsed
        ``rorg_func``/``rorg_type`` fields, which decode the documented EnOcean
        4BS teach-in EEP layout."""
        func = packet.rorg_func
        type_ = packet.rorg_type
        device = self._learn_unknown_device(packet, rorg=packet.rorg, func=func, type_=type_)
        # bidirectional devices expect a teach-in response so the pairing is
        # completed on both sides (we learned them, they learn us)
        if device is not None and (device.get('bidirectional') or device.get('smartack')):
            self._send_4bs_teachin_response(packet, func, type_)
        return device

    def _send_4bs_teachin_response(self, in_packet, func, type_):
        """reply to a 4BS teach-in telegram with an acknowledge teach-in.

        For bidirectional 4BS devices the sensor expects the controller to
        confirm the teach-in; we send a 4BS telegram back with the LRN bit
        set and the same EEP, targeting the device's sender address."""
        if self.enocean is None:
            logging.warning("cannot answer teach-in - no EnOcean gateway connected")
            return
        if getattr(in_packet, 'sender', None):
            destination = list(in_packet.sender)
        else:
            destination = None
        # answer from the next free sender ID (not the base ID) so the
        # bidirectional pairing uses an unused virtual address
        sender = None
        nxt = self.next_free_sender()
        if nxt is not None:
            sender = sender_bytes(nxt)
        else:
            sender = self.enocean_sender
        if destination is None:
            return
        try:
            packet = RadioPacket.create(RORG.BS4, func, type_,
                                        sender=sender,
                                        destination=destination,
                                        learn=True)
            # set the learn/teach-in bit (LRN) in DB0 and the contains-eep bit
            if len(packet.data) >= 5:
                packet.data[1] |= 0x88
            self.enocean.send(packet)
            logging.info("Sent 4BS teach-in response to %s (EEP %02X-%02X-%02X)",
                         enocean.utils.to_hex_string(destination), RORG.BS4, func, type_)
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Failed to send 4BS teach-in response: %s", exc)

    @staticmethod
    def _recognize_rps_eep(packet):
        """recognize the F6/RPS EEP from the telegram's data byte (D0).

        RPS telegrams do not carry an EEP, but the single data byte (data[1])
        encodes button/switch fields whose bit layout differs per EEP (from
        the EnOcean EEP.xml field offsets).

        Warning: the D0 bit spaces of the 2-rocker switch (F6-02-01) overlap
        with the smoke detector (F6-05-02) and leakage sensor (F6-05-01):
          0x10 / 0x30 = smoke alarm ON / battery low  BUT also rocker-2 press
          0x11        = water detected                BUT also rocker-2 press
        A single RPS telegram therefore cannot tell them apart unambiguously.
        Rocker switches are by far the most common F6 device and are exactly
        what a user triggers during teach-in, so ambiguous values are
        classified as the rocker switch. Only 0x70 (key card) is unique to a
        detector. Users with a real smoke/leakage sensor can correct the EEP
        in the edit dialog.

        Returns (func, type) or None.
        """
        if packet.rorg != RORG.RPS or len(packet.data) < 2:
            return None
        d0 = packet.data[1]

        # 0x70 is NOT a valid 2-rocker state (R2 would exceed 0..3) - it is
        # the Key Card Activated Switch (F6-04-01). Unambiguous.
        if d0 == 0x70:
            return (0x04, 0x01)   # Key Card Activated Switch

        # window handle uses only bits 2-3 (values 1..3): 0x04 / 0x0C.
        # These are not valid rocker presses (R1=0, EB=0/1, R2=0) and not
        # smoke/leakage values.
        if d0 in (0x04, 0x0C):
            return (0x10, 0x00)   # Window Handle

        # Any other D0 that carries R1 (bits 0-2), EB (bit 3), R2 (bits 4-6)
        # or SA (bit 7) is a 2-rocker switch (F6-02-01). This includes the
        # values 0x10/0x30/0x11 that also appear in smoke/leakage telegrams -
        # they are classified as rocker because that is the overwhelmingly
        # common and user-triggered F6 device.
        r2 = (d0 >> 4) & 0x07
        sa = (d0 >> 7) & 0x01
        r1 = (d0 >> 0) & 0x07
        if r2 != 0 or sa != 0 or (r1 != 0 and (d0 & 0x04) == 0):
            return (0x02, 0x01)   # Rocker Switch, 2 Rocker

        # remaining: push button (0x00 released, 0x08 pressed)
        return (0x01, 0x01)       # Push Button

    @staticmethod
    def _is_1bs_learn_telegram(packet):
        """a 1BS (D5) teach-in telegram.
        Uses the enocean library's ``packet.learn`` (active-low LRN bit in DB0)."""
        if packet.rorg != RORG.BS1 or len(packet.data) < 2:
            return False
        return bool(packet.learn)

    def _handle_1bs_learn_telegram(self, packet):
        """register a 1BS (D5) device from its learn telegram.
        1BS telegrams carry no EEP in the teach-in, so the device is added
        with the RORG's default profile (e.g. D5-00-01 Single Input Contact)."""
        return self._learn_unknown_device(packet, rorg=packet.rorg)

    def _handle_ute_packet(self, packet):
        """handle an incoming Universal Teach-In (UTE) telegram"""
        address = enocean.utils.combine_hex(packet.sender)
        address_hex = enocean.utils.to_hex_string(packet.sender)

        if not self.learn_mode:
            logging.debug("UTE telegram from %s ignored (teach-in disabled)", address_hex)
            return

        # UTE responses are handled by this class, keep the library from auto-answering
        if self.enocean is not None:
            self.enocean.teach_in = False

        existing = self._sensors_by_address.get(address)
        rorg = packet.rorg_of_eep
        func = packet.rorg_func
        type_ = packet.rorg_type

        if not existing:
            # UTE telegram without EEP information cannot be added automatically
            if rorg == RORG.UNDEFINED:
                logging.warning("UTE telegram from %s does not carry an EEP, "
                                "please add the sensor manually", address_hex)
                self.learn_mode = False
                return
            profile = self._eep_registry.get(rorg, func, type_)
            if profile is None:
                logging.warning("UTE telegram from %s carries unsupported EEP %s",
                                address_hex, eep_dash(rorg, func, type_))
            # generate a stable, unique name from the device address
            name = 'ute_' + format(address, '08x')
            self._store.add({'name': name, 'address': address,
                             'rorg': rorg, 'func': func, 'type': type_})
            new_sensor = self._load_dynamic_sensors(name)
            self._on_sensors_changed(new_sensor)
            logging.info("Teach-in: added sensor %s (EEP %s) address %s",
                         name, profile['eep'] if profile else 'unknown', address_hex)
        else:
            logging.info("Teach-in: device %s already known (%s)",
                         address_hex, ', '.join(s['name'] for s in existing))

        # Bidirectional devices expect a UTE teach-in response - acknowledge the learn
        if not packet.unidirectional:
            try:
                sender = self.enocean_sender or self.enocean.base_id
                response = packet.create_response_packet(sender)
                self.enocean.send(response)
                logging.info("Sent UTE teach-in response to %s", address_hex)
            except Exception as exc:   # pylint: disable=broad-except
                logging.error("Failed to send UTE teach-in response: %s", exc)

        # teach-in is one-shot: disable it after a device was received
        self.learn_mode = False

    # ------------------------------------------------------------------
    # MQTT CLIENT
    # ------------------------------------------------------------------
    def _on_connect(self, mqtt_client, _userdata, _flags, return_code):
        '''callback for when the client receives a CONNACK response from the MQTT server.'''
        if return_code == 0:
            logging.info("Succesfully connected to MQTT broker.")
            # listen to enocean send requests
            for cur_sensor in self.sensors:
                mqtt_client.subscribe(cur_sensor['name'] + '/req/#')
        else:
            logging.error("Error connecting to MQTT broker: %s",
                          self.CONNECTION_RETURN_CODE.get(return_code, return_code))

    def _on_disconnect(self, _mqtt_client, _userdata, return_code):
        '''callback for when the client disconnects from the MQTT server.'''
        if return_code == 0:
            logging.warning("Successfully disconnected from MQTT broker")
        else:
            logging.warning("Unexpectedly disconnected from MQTT broker: %s",
                            self.CONNECTION_RETURN_CODE.get(return_code, return_code))

    def _on_mqtt_message(self, _mqtt_client, _userdata, msg):
        '''the callback for when a PUBLISH message is received from the MQTT server.'''
        # search for sensor
        found_topic = False
        logging.debug("Got MQTT message: %s", msg.topic)

        # try to decode JSON payloads; plain byte payloads are handled as-is
        try:
            mqtt_payload = json.loads(msg.payload)
        except (ValueError, TypeError, UnicodeDecodeError):
            mqtt_payload = msg.payload

        if isinstance(mqtt_payload, dict):
            found_topic = self._mqtt_message_json(msg.topic, mqtt_payload)
        else:
            found_topic = self._mqtt_message_normal(msg)

        if not found_topic:
            logging.warning("Unexpected or erroneous MQTT message: %s: %s", msg.topic, msg.payload)

    # ------------------------------------------------------------------
    # MQTT TO ENOCEAN
    # ------------------------------------------------------------------
    def _mqtt_message_normal(self, msg):
        '''Handle received PUBLISH message from the MQTT server as a normal payload.'''
        found_topic = False
        for cur_sensor in self._get_sensors_for_topic(msg.topic):
            # get message topic
            prop = msg.topic[len(cur_sensor['name']+"/req/"):]
            # do we face a send request?
            if prop == "send":
                found_topic = True

                # Clear sent data, if requested by the send message
                # MQTT payload is binary data, thus we need to decode it
                clear = False
                raw_data = False
                for action in msg.payload.decode('UTF-8').lower().split('+'):
                    if action == "clear":
                        clear = True
                    elif action == "learn":
                        cur_sensor['learn'] = True
                    elif action == "raw_data":
                        raw_data = True

                # raw_data has not been validated by the send payload
                if 'raw_data' in cur_sensor and not raw_data:
                    del cur_sensor['raw_data']

                self._send_message(cur_sensor, clear)

            elif prop == "raw_data":
                found_topic = True
                cur_sensor['raw_data'] = msg.payload.decode('UTF-8')
            else:
                found_topic = True
                # parse message content
                value = None
                try:
                    value = int(msg.payload)
                except ValueError:
                    logging.warning("Cannot parse int value for %s: %s", msg.topic, msg.payload)
                    # Prevent storing undefined value, as it will trigger exception in EnOcean library
                    return False
                # store received data
                logging.debug("%s: %s=%s", cur_sensor['name'], prop, value)
                if 'data' not in cur_sensor:
                    cur_sensor['data'] = {}
                cur_sensor['data'][prop] = value

        return found_topic

    def _mqtt_message_json(self, mqtt_topic, mqtt_json_payload):
        '''Handle received PUBLISH message from the MQTT server as a JSON payload.'''
        found_topic = False
        for cur_sensor in self._get_sensors_for_topic(mqtt_topic):
            # get message topic
            prop = mqtt_topic[len(cur_sensor['name']+"/"):]
            # JSON payload shall be sent to '/req' topic
            if prop == "req":
                found_topic = True
                send = False
                clear = False

                # do we face a send request?
                if "send" in mqtt_json_payload.keys():
                    send = True
                    logging.debug("Send Payload: %s", mqtt_json_payload['send'])
                    # Decode packet handling actions
                    for action in mqtt_json_payload['send'].lower().split('+'):
                        # Check whether the data buffer shall be cleared
                        if action == "clear":
                            clear = True
                        elif action == "learn":
                            cur_sensor['learn'] = True
                        elif action == "raw_data":
                            if 'raw_data' in mqtt_json_payload:
                                cur_sensor['raw_data'] = mqtt_json_payload['raw_data']
                                del mqtt_json_payload['raw_data']

                    # Remove 'send' field as it is not part of EnOcean data
                    del mqtt_json_payload['send']

                # Parse message content
                for topic in list(mqtt_json_payload):
                    try:
                        mqtt_json_payload[topic] = int(mqtt_json_payload[topic])
                    except ValueError:
                        logging.warning("Cannot parse int value for %s: %s", topic, mqtt_json_payload[topic])
                        # Prevent storing undefined value, as it will trigger exception in EnOcean library
                        del mqtt_json_payload[topic]

                # Append received data to cur_sensor['data'].
                # This will keep the possibility to pass single topic/payload as done with
                # normal payload, even if JSON provides the ability to pass all topic/payload
                # in a single MQTT message.
                logging.debug("%s: %s=%s", cur_sensor['name'], prop, mqtt_json_payload)
                if 'data' not in cur_sensor:
                    cur_sensor['data'] = {}
                cur_sensor['data'].update(mqtt_json_payload)

                # Finally, send the message
                if send:
                    self._send_message(cur_sensor, clear)

            # The targeted sensor has been found and the MQTT message has been handled
            break

        return found_topic

    def _send_message(self, sensor, clear):
        '''Send received MQTT message (Property-based) to EnOcean.'''
        logging.debug("Trigger message to: %s", sensor['name'])
        destination = sender_bytes(sensor['address'])

        # Retrieve command from MQTT message and pass it to _send_packet()
        command = None
        command_shortcut = sensor.get('command')

        if command_shortcut:
            # the MQTT message must have set the command field
            if not sensor.get('data') or not sensor.get('data').get(command_shortcut):
                logging.warning('Command field %s must be set in MQTT message!', command_shortcut)
                return
            # Retrieve command id from MQTT message
            command = sensor['data'][command_shortcut]
            logging.debug('Retrieved command id from MQTT message: %s', hex(command))

        # Send the MQTT message
        self._send_packet(sensor, destination, command)

        # Clear sent data, if requested by the sent message
        if clear:
            logging.debug('Clearing data buffer.')
            del sensor['data']

        # Delete learn
        if 'learn' in sensor:
            del sensor['learn']

        # Delete raw_data if any
        if 'raw_data' in sensor:
            del sensor['raw_data']

    # ------------------------------------------------------------------
    # ENOCEAN TO MQTT
    # ------------------------------------------------------------------
    def _get_command_id(self, packet, sensor):
        '''interpret packet to retrieve command id from VLD packets'''
        # Retrieve the first defined EEP profile matching sensor RORG-FUNC-TYPE
        # As we take the first defined profile, this suppose that command is
        # ALWAYS at the same offset and ALWAYS has the same size.
        profile = packet.eep.find_profile(
            packet._bit_data, sensor['rorg'], sensor['func'], sensor['type'])

        if profile:
            # Loop over profile contents
            for source in list(profile):
               if not source.tag:
                   continue
               # Check the current shortcut matches the command shortcut
               if source.get('shortcut') == sensor.get('command'):
                   return packet.eep._get_raw(source, packet._bit_data)

        # If profile or command shortcut not found,
        # return None for default handling of the packet
        return None

    def _publish_mqtt(self, sensor, mqtt_json):
        '''Publish decoded packet content to MQTT'''
        mqtt_publish_json = str(sensor.get('publish_json')) in ("True", "true", "1")
        mqtt_publish_rssi = str(sensor.get('publish_rssi')) in ("True", "true", "1")
        retain = str(sensor.get('persistent')) in ("True", "true", "1")

        # optional grouping: split "channel" on '/' -> publish to channel topics
        channel_id = sensor.get('channel')
        channel_id = channel_id.split('/') if channel_id not in (None, '') else []

        # Auxiliary data RSSI
        aux_data = {}
        if mqtt_publish_rssi:
            if mqtt_publish_json:
                # Keep _RSSI_ out of groups
                if channel_id:
                    aux_data.update({"_RSSI_": mqtt_json['_RSSI_']})
            else:
                self.mqtt.publish(sensor['name']+"/_RSSI_", mqtt_json['_RSSI_'], retain=retain)
        # Delete RSSI if already handled
        if channel_id or not mqtt_publish_json or not mqtt_publish_rssi:
            del mqtt_json['_RSSI_']

        # Auxiliary data _DATE_
        if str(sensor.get('publish_date')) in ("True", "true", "1"):
            if channel_id:
                if mqtt_publish_json:
                    aux_data.update({"_DATE_": mqtt_json['_DATE_']})
                else:
                    self.mqtt.publish(sensor['name']+"/_DATE_", mqtt_json['_DATE_'], retain=retain)
        else:
            del mqtt_json['_DATE_']

        # Publish auxiliary data
        if aux_data:
            self.mqtt.publish(sensor['name'], json.dumps(aux_data), retain=retain)

        # Determine MQTT topic
        topic = sensor['name']
        for cur_id in channel_id:
            if mqtt_json.get(cur_id) not in (None, ''):
                topic += f"/{cur_id}{mqtt_json[cur_id]}"
                del mqtt_json[cur_id]

        # Publish packet data to MQTT
        value = json.dumps(mqtt_json)
        if mqtt_json not in (None, ""):
            logging.debug("%s: Sent MQTT: %s", topic, value)
        else:
            retain = True
            value = None
            logging.debug("Clearing retained packets")

        if mqtt_publish_json:
            self.mqtt.publish(topic, value, retain=retain)
        else:
            for prop_name, value in mqtt_json.items():
                self.mqtt.publish(f"{topic}/{prop_name}", value, retain=retain)

    def _read_packet(self, packet, sensor):
        '''interpret packet, read properties and publish to MQTT'''
        mqtt_json = {}

        # learn telegrams are published only when log_learn is enabled
        if not packet.learn or str(sensor.get('log_learn')) in ("True", "true", "1"):
            # underscore names can never collide with a real EEP field
            mqtt_json['_RSSI_'] = packet.dBm
            mqtt_json['_DATE_'] = packet.received.isoformat()

            found_property = self._handle_data_packet(packet, sensor, mqtt_json)
            if not found_property:
                logging.warning("message not interpretable: %s", sensor['name'])
            else:
                # Eltako FSB covers: accumulate + persist the absolute position
                # (running-time A5-3F-7F telegrams, absolute F6 0x70/0x50).
                address = enocean.utils.combine_hex(packet.sender)
                if sensor.get('category') == 'cover' or str(sensor.get('shut_time', '')).strip():
                    self._update_cover_position(address, sensor, mqtt_json)
                self._publish_mqtt(sensor, mqtt_json)
                # remember the latest decoded values for the web UI.
                # The LRN / LRNB learn bit is a protocol flag, not a measured
                # value - drop it from the UI (latest + graph history).
                ui_values = {k: v for k, v in mqtt_json.items()
                             if k not in ('LRN', 'LRNB')}
                self._latest_value[address] = {
                    'values': ui_values,
                    'ts': packet.received.isoformat() if packet.received else None,
                }
                # append to the rolling history for the value graph
                entry = {'values': ui_values,
                         'ts': packet.received.isoformat() if packet.received else None}
                hist = self._history.setdefault(address, [])
                hist.append(entry)
                if len(hist) > self._history_max:
                    del hist[:len(hist) - self._history_max]
        else:
            # learn request received
            logging.info("learn request not emitted to mqtt")

    def _update_cover_position(self, address, sensor, mqtt_json):
        """accumulate + persist the absolute cover position for an Eltako FSB.

        Publishes the position to the retained ``_ha/pos`` sub-topic (two
        levels deep so it stays invisible to the device's ``+`` subscribers)
        and the configured travel time to ``_ha/shut_time``.
        """
        try:
            prev = self._cover_store.get_position(address)
            shut_time = sensor.get('shut_time') or self.conf.get('shut_time') or 255
            pos = update_cover_position(prev, mqtt_json.get('_RAW_DATA_'),
                                        mqtt_json, shut_time)
            if pos is not None:
                self._cover_store.set_position(address, pos)
                # publish the retained absolute position HA reads
                if self.mqtt:
                    topic = sensor['name'] + POSITION_SUBTOPIC
                    self.mqtt.publish(topic, '{"POS": %d}' % pos, retain=True)
                    self.mqtt.publish(sensor['name'] + SHUT_TIME_SUBTOPIC,
                                      str(shut_time), retain=True)
                mqtt_json['POS'] = pos
        except Exception:   # pylint: disable=broad-except
            logging.exception("cover position update failed for %s",
                              sensor.get('name'))

    def _eep_decode(self, packet, sensor):
        """decode a telegram with the code-defined EEP engine.

        Returns {shortcut: {'value': ..., 'raw_value': ..., 'description': ...,
        'unit': ...}} (engine format) or {} when the profile is unknown or no
        case matches. Works for 4BS (A5), RPS (F6), 1BS (D5) and VLD (D2).
        """
        prof = eep_engine.find_profile(sensor.get('rorg'), sensor.get('func'), sensor.get('type'))
        if prof is None:
            return {}
        rorg = sensor.get('rorg')
        # payload bytes (exclude rorg byte, sender id, status) -> bit array
        if rorg in (RORG.BS4,):         # 4BS: 4 payload bytes
            payload = packet.data[1:5]
        elif rorg == RORG.VLD:          # VLD: variable payload, before sender+status
            payload = packet.data[1:len(packet.data) - 1 - 4]
        else:                            # RPS/BS1: 1 payload byte
            payload = packet.data[1:2]
        bit_data = to_bitarray(payload, 8 * len(payload))
        status = packet.status if getattr(packet, 'status', None) is not None else (packet.data[-1] if packet.data else 0)
        bit_status = to_bitarray([status & 0xFF], 8)
        case = eep_engine.select_case(prof, bit_data, bit_status)
        if case is None:
            return {}
        return eep_engine.decode(case, bit_data, bit_status)

    def _handle_data_packet(self, packet, sensor, mqtt_json):
        # radio packet of proper rorg type received; parse EEP
        found_property = False
        direction = None
        if sensor.get('direction'):
            direction = sensor.get('direction')

        # Retrieve command from the received packet and pass it to the engine
        command = None
        if sensor.get('command'):
            command = self._get_command_id(packet, sensor)
            if command:
                logging.debug('Retrieved command id from packet: %s', hex(command))

        # Decode the telegram with the code-defined EEP engine (validated
        # against the official EnOcean certification vectors). No hardcoded
        # EEP tables / bit layouts here.
        decoded = self._eep_decode(packet, sensor)

        # Now send also raw data to MQTT
        raw_data = packet.data[1:len(packet.data)-1-4]
        raw_data.append(packet.data[-1])
        mqtt_json["_RAW_DATA_"] = enocean.utils.to_hex_string(raw_data)

        # loop through all EEP properties
        for prop_name, cur_prop in decoded.items():
            found_property = True
            # we only extract numeric values, either the scaled ones
            # or the raw values for enums
            if isinstance(cur_prop['value'], numbers.Number):
                value = cur_prop['value']
            else:
                value = cur_prop['raw_value']
            # remember the textual representation + unit for the web UI
            self._field_meta.setdefault(enocean.utils.combine_hex(packet.sender), {})[prop_name] = {
                'unit': cur_prop.get('unit', ''),
                'description': cur_prop.get('description', ''),
                'text': str(cur_prop.get('value', value)),
            }
            # publish extracted information
            logging.debug("%s: %s (%s)=%s %s", sensor['name'], prop_name,
                            cur_prop['description'], cur_prop['value'], cur_prop['unit'])

            # Store property
            mqtt_json[prop_name] = value

        return found_property

    # ------------------------------------------------------------------
    # LOW LEVEL FUNCTIONS
    # ------------------------------------------------------------------
    def _reply_packet(self, in_packet, sensor):
        '''send enocean message as a reply to an incoming message'''
        # prepare addresses
        destination = in_packet.sender

        self._send_packet(sensor, destination, None, True,
                          in_packet.data if in_packet.learn else None)

    # ------------------------------------------------------------------
    # Bi-directional / smartACK support
    # ------------------------------------------------------------------
    def _needs_bidirectional_reply(self, packet, sensor):
        '''decide whether an incoming telegram expects a reply from us'''
        # explicit per-sensor configuration wins
        if str(sensor.get('answer')) in ("True", "true", "1"):
            return True
        # bidirectional devices reply to every telegram (A5-20 family, D2-01, ...)
        if sensor.get('bidirectional') or sensor.get('smartack'):
            return True
        # UTE teach-in telegrams always get an acknowledge response
        return packet.rorg == RORG.UTE

    def _build_smartack_reply(self, in_packet, sensor):
        '''construct a fast smartACK (VLD) acknowledge packet.

        D2-11-01 smartACK devices expect an answer within a few hundred
        milliseconds, otherwise they retransmit. We build a VLD packet with
        the same EEP back to the device, copying the CMD/value so the device
        sees an acknowledge. Falls back to None if the EEP is not usable.
        '''
        rorg = sensor.get('rorg')
        func = sensor.get('func')
        type_ = sensor.get('type')
        if rorg != RORG.VLD:
            return None

        # sender of the reply is our own base id
        if 'sender' in sensor:
            sender = sender_bytes(sensor['sender'])
        else:
            sender = self.enocean_sender

        try:
            packet = RadioPacket.create(
                sensor['rorg'], sensor['func'], sensor['type'],
                direction=2,  # outbound command
                sender=sender,
                destination=in_packet.sender,
                learn=False)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            # AttributeError covers profiles unknown to the enocean library
            logging.warning("Cannot build smartACK reply for %s: %s",
                            sensor['name'], exc)
            return None

        # copy the received payload bytes so the device sees a valid echo /
        # acknowledged state (the EEP values are echoed back unchanged)
        # data length is RORG + payload + sender(4) + status(1)
        n_payload = len(packet.data) - 1 - 4 - 1
        packet.data[1:1 + n_payload] = in_packet.data[1:1 + n_payload]
        packet.data[-1] = packet.status
        return packet

    def _build_raw_vld_reply(self, in_packet, sensor):
        '''fallback: build the smartACK reply as a raw VLD packet when the
        EEP is not known to the enocean library (e.g. D2-11-01).

        The payload is copied from the received telegram (device state echo),
        the destination is the device's sender address, and our base id is
        used as the new sender - exactly what smartACK devices expect.
        '''
        if in_packet.rorg != RORG.VLD:
            return None
        if 'sender' in sensor:
            sender = sender_bytes(sensor['sender'])
        else:
            sender = self.enocean_sender

        # data layout: RORG + payload (copied) + sender(4) + status(1)
        src = in_packet.data
        if len(src) < 6:
            return None
        payload = src[1:-5]          # payload bytes (before sender+status)
        data = [RORG.VLD] + list(payload) + list(sender) + [0]
        optional = [0x03] + list(in_packet.sender) + [0xFF, 0x00]
        try:
            packet = RadioPacket(PACKET.RADIO_ERP1, data=data, optional=optional)
        except (TypeError, ValueError) as exc:
            logging.warning("Cannot build raw VLD reply for %s: %s",
                            sensor['name'], exc)
            return None
        packet.rorg = RORG.VLD
        packet.sender = sender
        packet.destination = list(in_packet.sender)
        packet.learn = False
        return packet

    def _send_bidirectional_reply(self, in_packet, sensor):
        '''send a reply to a bidirectional / smartACK telegram.

        Returns True if a reply was sent, False if the device does not expect
        one or the reply could not be constructed.'''
        if not (sensor.get('smartack') or sensor.get('bidirectional') or
                str(sensor.get('answer')) in ("True", "true", "1")):
            return False
        if self.enocean is None:
            logging.warning("cannot reply - no EnOcean gateway connected")
            return False
        if sensor.get('smartack'):
            # fast path: construct and send immediately
            reply = self._build_smartack_reply(in_packet, sensor)
            if reply is None:
                reply = self._build_raw_vld_reply(in_packet, sensor)
            if reply is not None:
                try:
                    self.enocean.send(reply)
                    logging.info("sent smartACK reply to %s",
                                 enocean.utils.to_hex_string(in_packet.sender))
                    return True
                except Exception as exc:   # pylint: disable=broad-except
                    logging.error("Failed to send smartACK reply: %s", exc)
                    return False
            # fall through to the generic reply if construction failed
        # generic bi-directional / answer reply (4BS etc.)
        self._reply_packet(in_packet, sensor)
        return True

    def _send_packet(self, sensor, destination, command=None,
                     negate_direction=False, learn_data=None):
        '''triggers sending of an enocean packet'''
        # determine direction indicator
        if 'direction' in sensor and sensor.get('direction'):
            direction = sensor['direction']
            if negate_direction:
                # we invert the direction in this reply
                direction = 1 if direction == 2 else 2
        else:
            direction = None
        # is this a response to a learn packet?
        is_learn_response = learn_data is not None

        # Add possibility for the user to indicate a specific sender address
        # in sensor configuration using added 'sender' field.
        # So use specified sender address if any
        if 'sender' in sensor:
            sender = sender_bytes(sensor['sender'])
        else:
            sender = self.enocean_sender

        # Check whether the learn bit should be set in the packet (only valid for RORG.BS1 and RORG.BS4)
        force_learn = False
        if sensor.get('learn'):
            force_learn = True

        try:
            # Now pass command to RadioPacket.create()
            packet = RadioPacket.create(sensor['rorg'], sensor['func'], sensor['type'],
                                        direction=direction, command=command, sender=sender,
                                        destination=destination, learn=is_learn_response|force_learn)
        except ValueError as err:
            logging.error("Cannot create RF packet: %s", err)
            return

        # assemble data based on packet type (learn / data)
        if not is_learn_response:
            # data packet received
            # Check whether payload is raw data
            if 'raw_data' in sensor:
                logging.debug("sensor raw data: %s", sensor['raw_data'])
                try:
                    # Use the EnOcean library hex_string format for raw_data
                    # as there can be more than 8 bytes depending on EEP (VLD)
                    raw_data = enocean.utils.from_hex_string(sensor['raw_data'])
                    # Maximum raw data length including status byte
                    # -1 for RORG, -4 for sender ID
                    max_raw_data_len = len(packet.data)-1-4
                    # Status byte should not be set
                    if len(raw_data) == max_raw_data_len-1:
                        packet.data[1:max_raw_data_len] = raw_data
                    # Status byte should be set
                    elif len(raw_data) == max_raw_data_len:
                        packet.data[1:max_raw_data_len] = raw_data[:-1]
                        packet.data[-1] = raw_data[-1]
                    # Invalid raw data as all data bytes should be set
                    else:
                        raise Exception('Invalid raw data length. Should be in range [{}:{}]'.
                                        format(max_raw_data_len-1,max_raw_data_len))
                except Exception as ex:
                    logging.debug("Invalid raw data: %s (%s)", sensor['raw_data'], ex)
                    return
            else:
                # Initialize packet with default_data if specified
                if 'default_data' in sensor:
                    # Check default_data type
                    try:
                        # Default data is raw data
                        default_data = int(sensor['default_data'], 0)
                        packet.data[1:5] = sender_bytes(default_data)
                    except (ValueError, TypeError):
                        # Default data is property-based
                        logging.debug("sensor default data: %s", sensor['default_data'])
                        # Set packet data payload
                        packet.set_eep(json.loads(sensor['default_data']))
                        # Set packet status bits
                        packet.data[-1] = packet.status
                        # Ensure that the logging output of packet is updated
                        packet.parse_eep()

                # do we have specific data to send?
                if 'data' in sensor:
                    # override with specific data settings
                    logging.debug("sensor data: %s", sensor['data'])
                    # Set packet data payload with property-based data
                    packet.set_eep(sensor['data'])
                    # Set packet status bits
                    packet.data[-1] = packet.status
                    packet.parse_eep()  # ensure that the logging output of packet is updated
                else:
                    # what to do if we have no data to send yet?
                    logging.warning('sending only default data as answer to %s', sensor['name'])

        else:
            # learn request received
            # copy EEP and manufacturer ID
            packet.data[1:5] = learn_data[1:5]
            # update flags to acknowledge learn request
            packet.data[4] = 0xf0

        # send it
        logging.info("sending: %s", packet)
        if self.enocean is None:
            logging.warning("cannot send - no EnOcean gateway connected")
            return
        self.enocean.send(packet)

    def _send_teachin(self, name):
        """send a teach-in telegram to an existing actor so it learns this
        gateway as its controller.

        *Resolves* the stored sensor by name (bare or prefixed, model suffix
        hidden), then delegates to _send_teachin_payload(). Returns
        (ok, message).
        """
        sensor = None
        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        for s in self.sensors:
            # Match the name exactly (stored form) or as the web UI presents it
            # (bare display name - prefix stripped, model "/XX" suffix hidden).
            if (s.get('name') == name or s.get('name') == prefix + name
                    or self._display_name(s) == name):
                sensor = s
                break
        if sensor is None:
            return False, 'Device not found'
        return self._send_teachin_payload(
            name=sensor['name'],
            sender_hex=sensor.get('sender'),
            rorg=sensor.get('rorg'), func=sensor.get('func'), type_=sensor.get('type'),
            address=sensor.get('address'),
            category=self._classify_category(sensor),
            bidirectional=bool(sensor.get('bidirectional')))

    def _send_teachin_payload(self, name, sender_hex, rorg, func, type_,
                              address=None, category=None, bidirectional=None):
        """build and send a teach-in telegram from the raw device parameters.

        Works for 4BS actors (LRN bit set) and VLD actors (VLD teach-in
        packet). Used both for existing devices (resolved by name) and for
        not-yet-saved devices from the add-actor popup (sender + EEP are
        enough). Returns (ok, message).
        """
        # the transmitter identity in the teach-in telegram: an explicit
        # virtual sender (chosen in the add-actor popup) takes priority, else
        # the transceiver base id (matches how smartACK replies are sourced)
        if sender_hex:
            sender = sender_bytes(sender_hex)
        else:
            sender = self.enocean_sender

        # teach-in makes sense for actors and bi-directional devices (they can
        # receive and register the gateway). Plain one-way sensors cannot.
        # Resolve the category from the EEP registry if not provided.
        if category is None or bidirectional is None:
            profile = self._eep_registry.get(rorg, func, type_) if type_ is not None else None
            if profile:
                category = profile.get('category', 'sensor') if category is None else category
                bidirectional = profile.get('bidirectional', False) if bidirectional is None else bidirectional
        # A device can be taught-in when the UI treats it as an actor or
        # bidirectional (the web UI shows the teach-in button for both).
        is_actor = category in ('actor', 'bidirectional') or bidirectional
        if not is_actor:
            return False, 'Teach-in telegram is only supported for actors / bidirectional devices'

        # a teach-in needs a live transceiver - without one there is nothing
        # to send from and the gateway is still searching for its dongle
        if self.enocean is None or not self.enocean.is_alive():
            return False, 'EnOcean gateway not connected - teach-in unavailable'

        destination = sender_bytes(address) if address is not None else None

        # 4BS and VLD have a real teach-in telegram (LRN bit / UTE request).
        # Other RORGs (F6/RPS rockers, BS1) have no teach-in - send a regular
        # data/control telegram instead so the action can still reach the actor.
        is_teachable = rorg in (RORG.BS4, RORG.VLD)

        try:
            if rorg == RORG.VLD:
                # VLD teach-in: send a UTE (0xD4) teach-in REQUEST to the actor.
                # VLD devices learn their controller through a Universal
                # Teach-In telegram, not a regular VLD data telegram (the
                # latter also fails to build without a command/CMD value).
                # UTE data layout (see UTETeachInPacket.parse()):
                #   [0]=0xD4, [1]=request type (0x00 = TEACH_IN),
                #   [2]=channel (0), [3..4]=manufacturer id (0 = none),
                #   [5]=type, [6]=func, [7]=rorg of EEP (0xD2),
                #   [8..11]=sender, [12]=status
                payload = [RORG.UTE, UTETeachInPacket.TEACH_IN, 0,
                           0, 0, type_ or 0, func or 0, RORG.VLD]
                data = payload + list(sender) + [0]
                optional = ([0x03] + destination + [0xFF, 0]
                            if destination is not None else None)
                packet = UTETeachInPacket(PACKET.RADIO_ERP1,
                                          data=data, optional=optional)
                self.enocean.send(packet)
            elif rorg == RORG.BS4:
                # 4BS teach-in: set the LRN bit in DB0 (data[1] bit 3)
                packet = RadioPacket.create(RORG.BS4, func, type_,
                                            sender=sender,
                                            destination=destination,
                                            learn=True)
                # force the learn bit (LRN=1) in DB0
                if len(packet.data) >= 2:
                    packet.data[1] |= 0x08
                    packet.parse_eep(func, type_)
                self.enocean.send(packet)
            else:
                # No teach-in telegram exists for this RORG (F6/RPS, BS1, ...).
                # Send a regular control telegram instead so the "Send teach-in"
                # option always works; RadioPacket.create fills in the EEP's
                # default value (e.g. a rocker press for F6-02-01).
                packet = RadioPacket.create(rorg, func, type_,
                                            sender=sender,
                                            destination=destination,
                                            learn=False)
                packet.parse_eep(func, type_)
                self.enocean.send(packet)
            logging.info("Teach-in telegram sent to %s (%s)", name,
                         enocean.utils.to_hex_string(destination) if destination else 'broadcast')
            return True, 'Teach-in telegram sent' if is_teachable else 'Telegram sent'
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Failed to send teach-in to %s: %s", name, exc)
            return False, str(exc)

    def _handle_secure_packet(self, packet):
        """decrypt a VAES secure telegram (SEC 0x30 / SEC_ENCAPS 0x31) and
        route the recovered plaintext to normal packet handling.

        Requires a per-device configuration: the pre-shared key (hex) and an
        optional SLF/roaming window. The rolling code is persisted via
        SecureStore so replay protection survives restarts.
        """
        try:
            from enoceanmqtt.security import SecureDevice, decrypt_telegram, parse_slf
            address = enocean.utils.combine_hex(packet.sender)
            cfg = self._secure_config.get(address)
            if not cfg or not cfg.get('key'):
                logging.warning("secure telegram from %s but no key configured",
                                enocean.utils.to_hex_string(packet.sender))
                return
            key = bytes.fromhex(cfg['key'])
            slf = parse_slf(int(cfg.get('slf', '0x8B'), 0))
            rlc = self._secure_store.get_rlc(address)
            dev = SecureDevice(key=key, rlc=rlc, rlc_size=slf.rlc_size,
                               rlc_tx=slf.rlc_tx, cmac_len=slf.cmac_len)
            # secure DATA field = bytes between the RORG byte and the trailing
            # sender(4)+status(1); length = cmac(3/4) + enc (+ optional RLC)
            wire = bytes(packet.data[1:len(packet.data) - 5])
            inner = decrypt_telegram(dev, packet.rorg, wire)
            if inner is None:
                logging.warning("secure telegram from %s failed auth (RLC/CMAC)",
                                enocean.utils.to_hex_string(packet.sender))
                return
            self._secure_store.set_rlc(address, dev.rlc)
            inner_rorg, inner_data = inner
            if inner_rorg is None:
                # RORG-less RPS/PTM payload nibble
                inner_rorg = 0xF6
            # route the recovered bytes as a normal radio telegram
            # (data layout: rorg, payload, sender, status)
            payload = list(inner_data)
            status = packet.data[-1] if packet.data else 0
            data = [inner_rorg] + payload + list(packet.sender) + [status]
            try:
                from enocean.protocol.packet import RadioPacket
                from enocean.protocol.constants import PACKET
                recovered = RadioPacket(PACKET.RADIO_ERP1, data=data,
                                        optional=list(getattr(packet, 'optional', []) or []))
                recovered.parse()
                recovered.received = getattr(packet, 'received', None)
                logging.info("decrypted secure telegram -> RORG 0x%02X from %s",
                             inner_rorg, enocean.utils.to_hex_string(packet.sender))
                self._process_radio_packet(recovered)
            except Exception as exc:   # pylint: disable=broad-except
                logging.error("secure decode routing failed: %s", exc)
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("secure telegram handling failed: %s", exc)

    def _process_radio_packet(self, packet):
        # Universal Teach-In telegrams are handled separately
        if packet.rorg == RORG.UTE:
            self._handle_ute_packet(packet)
            return

        # secure telegrams (SEC / SEC_ENCAPS) - AES/VAES + rolling code
        if packet.rorg in (RORG.SEC, RORG.SEC_ENCAPS):
            self._handle_secure_packet(packet)
            return

        address = enocean.utils.combine_hex(packet.sender)

        # first, look whether we have this sensor configured
        found_sensor = False
        potential_sensors = self._sensors_by_address.get(address, [])

        for cur_sensor in potential_sensors:
            # Does this sensor match?
            if (packet.rorg == cur_sensor.get('rorg')) or \
               (not cur_sensor.get('rorg') and cur_sensor.get('ignore')):
                found_sensor = cur_sensor
                break

        # skip ignored sensors
        if found_sensor and 'ignore' in found_sensor and found_sensor['ignore']:
            return

        # log packet, if not disabled
        if str(self.conf.get('log_packets')) in ("True", "true", "1"):
            logging.info("received: %s", packet)

        # teach-in: if learn mode is active and the device is unknown, capture it.
        # Only a genuine teach-in is added (the user pressed the teach-in button):
        #  - UTE (0xD4) is always a teach-in telegram.
        #  - 4BS (0xA5) / 1BS (0xD5) carry the learn bit (LRN) in DB0 bit 3.
        #  - RPS (0xF6) has no learn bit - the (repeated) button/data telegram
        #    itself is the teach-in, so capture any telegram in learn mode.
        #  - VLD (0xD2) uses UTE for teach-in - a regular VLD data telegram is
        #    NOT a teach-in and is never auto-captured.
        #  - Anything else (data telegrams without the learn bit) is NOT added.
        replied_teachin = False
        if not found_sensor and self.learn_mode:
            if self._is_4bs_learn_telegram(packet):
                # 4BS learn telegram (LRN bit set): add + reply if bidirectional
                self._handle_4bs_learn_telegram(packet)
                replied_teachin = True
            elif packet.rorg == RORG.BS1 and self._is_1bs_learn_telegram(packet):
                self._handle_1bs_learn_telegram(packet)
            elif packet.rorg == RORG.RPS:
                # RPS (F6 rockers) send data-only telegrams as their teach-in
                # (no learn bit available) - capture on any telegram
                self._learn_unknown_device(packet)
            # 4BS/1BS data telegrams without the learn bit, and VLD data
            # telegrams, are intentionally NOT captured here.
            # teach-in is one-shot for this press - a regular telegram does not
            # carry a response flag, so keep learn mode on until the cycle ends
            # and let the device be tracked once it matches below.
            potential_sensors = self._sensors_by_address.get(address, [])
            for cur_sensor in potential_sensors:
                if packet.rorg == cur_sensor.get('rorg'):
                    found_sensor = cur_sensor
                    break

        # abort loop if sensor not found
        if not found_sensor:
            logging.info("unknown sensor: %s (RORG = %s)",
                         enocean.utils.to_hex_string(packet.sender), hex(packet.rorg))
            return

        # track last-seen for the web interface
        self._last_seen[address] = datetime.datetime.utcnow()

        # the enocean library sets learn=True by default; only 1BS/4BS handle
        # the learn flag correctly. VLD devices use UTE for teach-in and RPS
        # devices only send data telegrams, so clear the flag for both.
        if found_sensor['rorg'] == RORG.VLD and packet.rorg != RORG.UTE:
            packet.learn = False
        elif found_sensor['rorg'] == RORG.RPS:
            packet.learn = False

        # smartACK devices (e.g. D2-11-01) expect the reply within a tight
        # window (~a few hundred ms), so answer BEFORE the (slower) MQTT
        # publish cycle. Other bidirectional devices get the reply after the
        # packet has been published.
        needs_reply = (not replied_teachin) and \
            self._needs_bidirectional_reply(packet, found_sensor)
        if needs_reply and found_sensor.get('smartack'):
            self._send_bidirectional_reply(packet, found_sensor)

        # interpret packet, read properties and publish to MQTT
        self._read_packet(packet, found_sensor)

        # check for necessary reply (non-smartACK path)
        if needs_reply and not found_sensor.get('smartack'):
            self._send_bidirectional_reply(packet, found_sensor)


    # ------------------------------------------------------------------
    # RUN LOOP
    # ------------------------------------------------------------------
    def request_restart(self):
        """set a flag so the run loop exits cleanly; the process supervisor
        (docker/systemd) restarts the gateway."""
        self._restart_requested = True
        return {'ok': True, 'message': 'Restart scheduled'}

    def _query_diagnostics(self):
        """send ESP3 common commands to read transceiver diagnostics.

        Rate-limited: a single round of three COMMON_COMMANDs is sent at most
        once per DIAGNOSTICS_INTERVAL, and only when the previous round was
        answered (a dead/unresponsive dongle would otherwise queue three
        packets every minute forever, growing the transmit backlog + serial
        writes without any benefit).
        """
        if not self.enocean:
            return
        now = time.time()
        if now - self._diag.get('_last_query', 0) < DIAGNOSTICS_INTERVAL:
            return
        if self._diag.get('_last_query') is not None and not self._diag.get('_query_answered'):
            return  # previous round got no response; do not pile up more
        self._diag['_last_query'] = now
        self._diag['_query_answered'] = False
        try:
            for code in (CO_RD_VERSION, CO_RD_REPEATER, CO_RD_DUTYCYCLE_LIMIT):
                self.enocean.send(Packet(PACKET.COMMON_COMMAND, data=[code]))
        except Exception:   # pylint: disable=broad-except
            logging.exception("failed to send diagnostics query")

    def _publish_diagnostics(self):
        """publish the current diagnostics as a retained JSON diagnostic topic."""
        try:
            if self.mqtt and self.mqtt.is_connected():
                topic = self.conf.get('mqtt_prefix', 'enocean/') + '__system/diagnostics'
                self.mqtt.publish(topic, json.dumps(self.diagnostics), retain=True)
        except Exception:   # pylint: disable=broad-except
            pass

    def _handle_response(self, packet):
        """parse a COMMON_COMMAND RESPONSE packet into the diagnostics state.

        The RESPONSE packet carries ``response`` (return code) and
        ``response_data``. Because the stick does not echo the requested
        command code, each response is recognised by its fixed ESP3 length:
          CO_RD_VERSION        -> 16+ bytes (app/api/chip_id/chip_ver/...)
          CO_RD_REPEATER       -> 2 bytes  (REP_ENABLE, REP_LEVEL)
          CO_RD_DUTYCYCLE_LIMIT-> 1 byte   (available %)
        """
        response_code = getattr(packet, 'response', None)
        if response_code != RETURN_CODE.OK:
            logging.debug("diagnostics response not OK: %s", response_code)
            return
        # mark the current diagnostics round as answered so the next one may
        # be sent (see _query_diagnostics rate limiting)
        self._diag['_query_answered'] = True
        rd = list(getattr(packet, 'response_data', []) or [])
        if len(rd) >= 16:
            app, api, chip = parse_version(rd)
            if chip:
                self._diag['app_version'] = app
                self._diag['api_version'] = api
                self._diag['chip_id'] = chip
                logging.info("EnOcean transceiver: app %s, API %s, chip %s",
                             app, api, chip)
        elif len(rd) == 2:
            level = parse_repeater(rd)
            self._diag['repeater_level'] = level
            logging.info("EnOcean repeater: %s",
                         "off" if not level else "level %d" % level)
        elif len(rd) == 1:
            self._diag['duty_cycle_available'] = parse_duty_cycle(rd)
            logging.info("EnOcean TX duty-cycle available: %s%%",
                         self._diag['duty_cycle_available'])

    def _handle_event(self, packet):
        """react to ESP3 EVENT packets (duty-cycle limit, transmit failed)."""
        ev = getattr(packet, 'event', None)
        if ev == EV_DUTYCYCLE_LIMIT:
            self._diag['duty_cycle_available'] = 0
            logging.warning("EnOcean TX duty-cycle limit reached; transmits throttled")
        elif ev == EV_TRANSMIT_FAILED:
            self._diag['transmit_failures'] = self._diag.get('transmit_failures', 0) + 1
            logging.warning("EnOcean transmit failed (%d total)",
                            self._diag['transmit_failures'])

    def run(self):
        """the main loop with blocking enocean packet receive handler

        The loop never exits when the transceiver is temporarily
        unavailable - it waits and reconnects (exponential backoff) so the
        web UI stays up for configuration. Only a process supervisor restart
        or a keyboard interrupt stops it.
        """
        # start endless loop for listening
        _reconnect_delay = 1
        while not self._restart_requested:
            # (re)connect the transceiver if the previous one died. The
            # gateway also boots without one: until a gateway appears the
            # loop simply sleeps so the web UI (and MQTT) stay available.
            if self.enocean is None or not self.enocean.is_alive():
                if self.enocean is None:
                    # first failure at boot, or the configured port is still
                    # unplugged/unreachable - keep MQTT alive and retry
                    logging.warning("No EnOcean gateway available; retrying in "
                                    "%ds (gateway discovery active)",
                                    _reconnect_delay)
                    time.sleep(_reconnect_delay)
                    _reconnect_delay = min(_reconnect_delay * 2, 30)
                    try:
                        self._connect_enocean()
                    except Exception as exc:   # pylint: disable=broad-except
                        logging.error("EnOcean gateway not available: %s", exc)
                        self.enocean_error = str(exc) or exc.__class__.__name__
                        self.enocean = None
                        continue
                    _reconnect_delay = 1
                    if self.enocean is not None:
                        self.enocean.teach_in = False
                else:
                    try:
                        logging.warning("EnOcean transceiver not alive; reconnecting "
                                        "in %ds", _reconnect_delay)
                        time.sleep(_reconnect_delay)
                        _reconnect_delay = min(_reconnect_delay * 2, 30)
                        self._reconnect_enocean()
                    except Exception as exc:   # pylint: disable=broad-except
                        logging.error("EnOcean reconnect failed: %s", exc)
                        continue
                    _reconnect_delay = 1
            # Request transmitter ID, if needed. Only when the transceiver is truly
            # usable: for a TCP endpoint that is down the retry thread is alive
            # but there is no socket, and querying base_id would enqueue a
            # CO_RD_IDBASE every loop iteration forever (the queue can never be
            # drained without a connection).
            if self.enocean is not None and self.gateway_connected() and self.enocean_sender is None:
                self.enocean_sender = self.enocean.base_id
                # now that we know the base id, query transceiver diagnostics
                self._query_diagnostics()

            # Loop to empty the queue...
            try:
                # get next packet
                packet = self.enocean.receive.get(block=True, timeout=1)

                # check packet type
                if packet.packet_type == PACKET.RADIO:
                    self._process_radio_packet(packet)
                elif packet.packet_type == PACKET.RESPONSE:
                    response_code = RETURN_CODE(packet.data[0]) if packet.data else None
                    logging.info("got response packet: %s", response_code.name if response_code else packet)
                    self._handle_response(packet)
                elif packet.packet_type == PACKET.EVENT:
                    logging.info("got event packet: %s", packet)
                    self._handle_event(packet)
                else:
                    logging.info("got non-RF packet: %s", packet)
                    continue
            except queue.Empty:
                # periodically re-query + re-publish transceiver diagnostics.
                # _query_diagnostics() itself rate-limits (incl. dead-dongle
                # backoff), so this is just the outer throttle.
                if (self.gateway_connected() and
                        time.time() - self._diag.get('_last_query', 0) > DIAGNOSTICS_INTERVAL):
                    self._query_diagnostics()
                    self._publish_diagnostics()
                continue
            except KeyboardInterrupt:
                logging.debug("Exception: KeyboardInterrupt")
                break

        # Run finished, close MQTT client and stop Enocean thread
        logging.debug("Cleaning up")
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        self.mqtt.loop_forever()  # will block until disconnect complete
        if self.enocean is not None:
            self.enocean.stop()

# Copyright (c) 2020 embyt GmbH. See LICENSE for further details.
# Author: Roman Morawek <roman.morawek@embyt.com>
"""this class handles the enocean and mqtt interfaces"""
import logging
import queue
import numbers
import json
import platform
import os
import datetime

from enocean.communicators.serialcommunicator import SerialCommunicator
from enoceanmqtt.tcpclientcommunicator import TCPClientCommunicator
from enocean.protocol.packet import RadioPacket, UTETeachInPacket
from enocean.protocol.constants import PACKET, RETURN_CODE, RORG
import enocean.utils
import paho.mqtt.client as mqtt

from enoceanmqtt.sensor_store import SensorStore
from enoceanmqtt.eep_registry import get_registry


class Communicator:
    """the main working class providing the MQTT interface to the enocean packet classes"""
    mqtt = None
    enocean = None

    #: a sensor is considered "online" if it has been seen within this window
    ONLINE_TIMEOUT_SECONDS = 10 * 60

    CONNECTION_RETURN_CODE = [
        "connection successful",
        "incorrect protocol version",
        "invalid client identifier",
        "server unavailable",
        "bad username or password",
        "not authorised",
    ]

    def __init__(self, config, sensors):
        self.conf = config
        self.sensors = sensors
        self._index_sensors()

        # UTE teach-in state (managed through the web interface / MQTT learn)
        self.learn_mode = False
        self._last_seen = {}
        # latest decoded values per device address (for the web UI)
        self._latest_value = {}
        # per-field unit/description metadata per device address
        self._field_meta = {}
        # rolling history of decoded values (for the value graph)
        self._history = {}
        self._history_max = int(self.conf.get('webui_history', 200))

        # EEP catalog + persistent store for web-added sensors
        self._eep_registry = get_registry()
        self._store = SensorStore(self._resolve_sensor_store_path())
        self._dynamic_names = set()
        self._load_dynamic_sensors()

        # check for mandatory configuration
        if 'mqtt_host' not in self.conf or 'enocean_port' not in self.conf:
            raise Exception("Mandatory configuration not found: mqtt_host/enocean_port")
        mqtt_port = int(self.conf['mqtt_port']) if 'mqtt_port' in self.conf else 1883
        mqtt_keepalive = int(self.conf['mqtt_keepalive']) if 'mqtt_keepalive' in self.conf else 60

        # setup mqtt connection
        client_id = self.conf['mqtt_client_id'] if 'mqtt_client_id' in self.conf else ''
        self.mqtt = mqtt.Client(client_id=client_id)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect
        self.mqtt.on_message = self._on_mqtt_message
        self.mqtt.on_publish = self._on_mqtt_publish
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

        # setup enocean communication
        eport  = self.conf['enocean_port']
        seport = eport.split(':')
        if seport[0] == "tcp":
            logging.info("connecting TCPClient to %s port %d", seport[1],int(seport[2]))
            self.enocean = TCPClientCommunicator(seport[1],int(seport[2]))
        else:
            logging.info("connecting Serial to %s", eport)
            self.enocean = SerialCommunicator(eport)

        self.enocean.start()
        # sender will be automatically determined
        self.enocean_sender = None
        # UTE teach-in telegrams are handled by this class, not by the library
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
            if current in self._sensors_by_name:
                # Check if it matches name + "/" as in original code
                if topic.startswith(current + "/"):
                    matched.extend(self._sensors_by_name[current])
        # Sort matched sensors by their original order to maintain behavior
        if len(matched) > 1:
            matched.sort(key=lambda s: self._sensor_to_index.get(id(s), 0))
        return matched

    #=============================================================================================
    # SENSOR STORE / WEB-ADDED SENSORS
    #=============================================================================================
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
        for key in ('category', 'bidirectional', 'smartack'):
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
        return added[0] if added else None

    def add_sensor(self, payload):
        """add a sensor (manual or web interface) and persist it"""
        try:
            name = str(payload.get('name', '')).strip()
            address = payload.get('address')
            eep = str(payload.get('eep', '')).strip()
            sender = payload.get('sender')

            if not name:
                return {'ok': False, 'error': 'A sensor name is required'}
            if not isinstance(address, int):
                try:
                    address = int(str(address), 0)
                except (TypeError, ValueError):
                    return {'ok': False, 'error': 'Invalid sensor address'}
            if not (0 <= address <= 0xFFFFFFFF):
                return {'ok': False, 'error': 'Sensor address out of range'}

            parts = [p for p in eep.replace('0x', '').split('-') if p] if eep else []
            if len(parts) != 3:
                return {'ok': False, 'error': 'Invalid EEP, expected e.g. A5-02-05'}
            try:
                rorg = int(parts[0], 16)
                func = int(parts[1], 16)
                type_ = int(parts[2], 16)
            except ValueError:
                return {'ok': False, 'error': 'Invalid EEP'}

            prefix = self.conf.get('mqtt_prefix', 'enocean/')
            full_name = prefix + name
            if any(s.get('name') == full_name for s in self.sensors):
                return {'ok': False, 'error': 'A sensor with this name already exists'}

            stored = {'name': name, 'address': address,
                      'rorg': rorg, 'func': func, 'type': type_}
            if sender:
                try:
                    stored['sender'] = int(str(sender), 0)
                except (TypeError, ValueError):
                    return {'ok': False, 'error': 'Invalid sender address'}
            # optional per-sensor overrides (e.g. mark an EEP as bidirectional)
            for key in ('category', 'bidirectional', 'smartack', 'virtual'):
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
            return {'ok': False, 'error': 'Sensor not found'}

        changes = {}
        # optional rename
        new_name = payload.get('name')
        if new_name is not None:
            new_name = str(new_name).strip()
            if not new_name:
                return {'ok': False, 'error': 'A sensor name is required'}
            prefix = self.conf.get('mqtt_prefix', 'enocean/')
            if new_name != name and any(
                    s.get('name') == prefix + new_name for s in self.sensors):
                return {'ok': False, 'error': 'A sensor with this name already exists'}
            changes['name'] = new_name

        # optional address change
        address = payload.get('address')
        if address is not None:
            if not isinstance(address, int):
                try:
                    address = int(str(address), 0)
                except (TypeError, ValueError):
                    return {'ok': False, 'error': 'Invalid sensor address'}
            if not (0 <= address <= 0xFFFFFFFF):
                return {'ok': False, 'error': 'Sensor address out of range'}
            changes['address'] = address

        # optional EEP change
        eep = payload.get('eep')
        if eep is not None:
            eep = str(eep).strip()
            parts = [p for p in eep.replace('0x', '').split('-') if p] if eep else []
            if len(parts) != 3:
                return {'ok': False, 'error': 'Invalid EEP, expected e.g. A5-08-01'}
            try:
                changes['rorg'] = int(parts[0], 16)
                changes['func'] = int(parts[1], 16)
                changes['type'] = int(parts[2], 16)
            except ValueError:
                return {'ok': False, 'error': 'Invalid EEP'}

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
            return {'ok': True}
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("save_config failed: %s", exc)
            return {'ok': False, 'error': str(exc)}

    def describe_sensor(self, sensor):
        """build a JSON-friendly status description of a sensor"""
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
            eep = f'{rorg:02X}-{func:02X}-{type_:02X}' if func is not None and type_ is not None \
                  else f'{rorg:02X}'
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

        prefix = self.conf.get('mqtt_prefix', 'enocean/')
        display_name = sensor['name']
        if display_name.startswith(prefix):
            display_name = display_name[len(prefix):]
        # model-based sensors get an internal "/XX" rorg suffix - hide it
        if sensor.get('model') and len(display_name) > 3 and display_name[-3] == '/':
            display_name = display_name[:-3]

        latest = self._latest_value.get(address)
        field_meta = self._field_meta.get(address, {})
        if latest is not None:
            latest = dict(latest)
            latest['meta'] = field_meta

        return {
            'name': display_name,
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

    def eep_catalog(self):
        """return the list of known EnOcean equipment profiles"""
        return [{'eep': p['eep'], 'name': p['name'], 'rorg_name': p['rorg_name'],
                 'category': p.get('category', 'sensor'),
                 'bidirectional': bool(p.get('bidirectional')),
                 'smartack': bool(p.get('smartack'))}
                for p in self._eep_registry.profiles]

    @property
    def enocean_sender_hex(self):
        if self.enocean_sender is None:
            return None
        return enocean.utils.to_hex_string(self.enocean_sender)

    def virtual_senders(self):
        """the usable virtual sender IDs of the transceiver.

        EnOcean transceivers (USB300/TCM515 and similar) expose a 32-bit base
        ID plus a range of 128 assignable addresses (base .. base+127). These
        are used as the sender ID when transmitting to actors.
        Returns a list of ints (empty until the base ID is known).
        """
        base = self.enocean_sender or getattr(self.enocean, 'base_id', None)
        if base is None:
            return []
        base_int = enocean.utils.combine_hex(base)
        return [base_int + i for i in range(128)]

    #=============================================================================================
    # UNIVERSAL TEACH-IN (UTE)
    #=============================================================================================
    def set_learn_mode(self, enabled):
        """enable or disable UTE teach-in mode (one-shot)"""
        self.learn_mode = bool(enabled)
        # UTE responses are handled by this class, keep the library from auto-answering
        self.enocean.teach_in = False
        logging.info("UTE teach-in mode %s", "enabled" if self.learn_mode else "disabled")

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
        stored = {'name': name, 'address': address, 'rorg': rorg}
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
            # the RORG is used. Either way the device is immediately usable
            # after teach-in (no manual edit required) and can be refined
            # with the edit button.
            recognized = self._recognize_rps_eep(packet) if rorg == RORG.RPS else None
            if recognized:
                stored['func'] = recognized[0]
                stored['type'] = recognized[1]
            else:
                default = self._eep_registry.default_for_rorg(rorg)
                if default:
                    stored['func'] = default['func']
                    stored['type'] = default['type']
        self._store.add(stored)
        new_sensor = self._load_dynamic_sensors(name)
        if new_sensor is not None:
            self._on_sensors_changed(new_sensor)
        logging.info("Teach-in: captured device %s (RORG %s, EEP %s)",
                     address_hex, hex(rorg),
                     stored.get('func') is not None and
                     '%02X-%02X-%02X' % (rorg, stored['func'], stored['type']) or 'unknown')

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
        if getattr(in_packet, 'sender', None):
            destination = list(in_packet.sender)
        else:
            destination = None
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
        the EnOcean EEP.xml field offsets). Values that are unique to a
        specific EEP (smoke, leakage, key card) are matched exactly; the
        rocker switch / window handle / push button are told apart by which
        bit groups are used. Returns (func, type) or None.
        """
        if packet.rorg != RORG.RPS or len(packet.data) < 2:
            return None
        d0 = packet.data[1]

        # exact value matches (unique to one EEP)
        if d0 == 0x70:
            return (0x04, 0x01)   # Key Card Activated Switch
        if d0 == 0x11:
            return (0x05, 0x01)   # Liquid Leakage Sensor
        if d0 in (0x10, 0x30):
            return (0x05, 0x02)   # Smoke Detector

        # window handle uses only bits 2-3 (values 1..3)
        if d0 in (0x04, 0x0C):
            return (0x10, 0x00)   # Window Handle

        # 2-rocker switch: R2 (bits 4-6) or SA (bit 7) set, or R1 (bits 0-2)
        # set without bit 2 (bit 2 belongs to the window handle)
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
                logging.warning("UTE telegram from %s carries unsupported EEP "
                                "%02X-%02X-%02X", address_hex, rorg, func, type_)
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

    #=============================================================================================
    # MQTT CLIENT
    #=============================================================================================
    def _on_connect(self, mqtt_client, _userdata, _flags, return_code):
        '''callback for when the client receives a CONNACK response from the MQTT server.'''
        if return_code == 0:
            logging.info("Succesfully connected to MQTT broker.")
            # listen to enocean send requests
            for cur_sensor in self.sensors:
                # logging.debug("MQTT subscribing: %s", cur_sensor['name']+'/req/#')
                mqtt_client.subscribe(cur_sensor['name']+'/req/#')
        else:
            logging.error("Error connecting to MQTT broker: %s",
                          self.CONNECTION_RETURN_CODE[return_code]
                          if return_code < len(self.CONNECTION_RETURN_CODE) else return_code)

    def _on_disconnect(self, _mqtt_client, _userdata, return_code):
        '''callback for when the client disconnects from the MQTT server.'''
        if return_code == 0:
            logging.warning("Successfully disconnected from MQTT broker")
        else:
            logging.warning("Unexpectedly disconnected from MQTT broker: %s",
                            self.CONNECTION_RETURN_CODE[return_code]
                            if return_code < len(self.CONNECTION_RETURN_CODE) else return_code)

    def _on_mqtt_message(self, _mqtt_client, _userdata, msg):
        '''the callback for when a PUBLISH message is received from the MQTT server.'''
        # search for sensor
        found_topic = False
        logging.debug("Got MQTT message: %s", msg.topic)

        # Get how to handle MQTT message
        try:
            mqtt_payload = json.loads(msg.payload)
        except:
            mqtt_payload = msg.payload

        if isinstance(mqtt_payload, dict):
            found_topic = self._mqtt_message_json(msg.topic, mqtt_payload)
        else:
            found_topic = self._mqtt_message_normal(msg)

        if not found_topic:
            logging.warning("Unexpected or erroneous MQTT message: %s: %s", msg.topic, msg.payload)

    def _on_mqtt_publish(self, _mqtt_client, _userdata, _mid):
        '''the callback for when a PUBLISH message is successfully sent to the MQTT server.'''
        #logging.debug("Published MQTT message "+str(mid))


    #=============================================================================================
    # MQTT TO ENOCEAN
    #=============================================================================================
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
                if send == True:
                    self._send_message(cur_sensor, clear)

            # The targeted sensor has been found and the MQTT message has been handled
            break

        return found_topic

    def _send_message(self, sensor, clear):
        '''Send received MQTT message (Property-based) to EnOcean.'''
        logging.debug("Trigger message to: %s", sensor['name'])
        destination = [(sensor['address'] >> i*8) &
                       0xff for i in reversed(range(4))]

        # Retrieve command from MQTT message and pass it to _send_packet()
        command = None
        command_shortcut = sensor.get('command')

        if command_shortcut:
            # Check MQTT message sets the command field
            #if 'data' not in sensor or command_shortcut not in sensor['data'] or sensor['data'][command_shortcut] is None:
            if not sensor.get('data') or not sensor.get('data').get(command_shortcut):
                logging.warning(
                    'Command field %s must be set in MQTT message!', command_shortcut)
                return
            # Retrieve command id from MQTT message
            command = sensor['data'][command_shortcut]
            logging.debug('Retrieved command id from MQTT message: %s', hex(command))

        # Send the MQTT message
        self._send_packet(sensor, destination, command)

        # Clear sent data, if requested by the sent message
        if clear == True:
            logging.debug('Clearing data buffer.')
            del sensor['data']

        # Delete learn
        if 'learn' in sensor:
            del sensor['learn']

        # Delete raw_data if any
        if 'raw_data' in sensor:
            del sensor['raw_data']

    #=============================================================================================
    # ENOCEAN TO MQTT
    #=============================================================================================
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
        # Publish using JSON format ?
        mqtt_publish_json = str(sensor.get('publish_json')) in ("True", "true", "1")

        # Publish RSSI ?
        mqtt_publish_rssi = str(sensor.get('publish_rssi')) in ("True", "true", "1")

        # Retain the to-be-published message ?
        retain = str(sensor.get('persistent')) in ("True", "true", "1")

        # Is grouping enabled on this sensor
        channel_id = sensor.get('channel')
        channel_id = channel_id.split('/') if channel_id not in (None, '') else []

        # Handling Auxiliary data RSSI
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

        # Handling Auxiliary data _DATE_
        if str(sensor.get('publish_date')) in ("True", "true", "1"):
            # Publish _DATE_ both at device and group levels
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
        
        # Shall the packet be published to MQTT ?
        if not packet.learn or str(sensor.get('log_learn')) in ("True", "true", "1"):
            # Store RSSI
            # Use underscore so that it is unique and doesn't
            # match a potential future EnOcean EEP field.
            mqtt_json['_RSSI_'] = packet.dBm

            # Store receive date
            # Use underscore so that it is unique and doesn't
            # match a potential future EnOcean EEP field.
            mqtt_json['_DATE_'] = packet.received.isoformat()

            # Handling received data packet
            found_property = self._handle_data_packet( packet, sensor, mqtt_json)
            if not found_property:
                logging.warning("message not interpretable: %s", sensor['name'])
            else:
                self._publish_mqtt(sensor, mqtt_json)
                # remember the latest decoded values for the web UI
                address = enocean.utils.combine_hex(packet.sender)
                self._latest_value[address] = {
                    'values': dict(mqtt_json),
                    'ts': packet.received.isoformat() if packet.received else None,
                }
                # append to the rolling history for the value graph
                hist = self._history.setdefault(address, [])
                hist.append({'values': dict(mqtt_json),
                             'ts': packet.received.isoformat() if packet.received else None})
                if len(hist) > self._history_max:
                    del hist[:len(hist) - self._history_max]
        else:
            # learn request received
            logging.info("learn request not emitted to mqtt")

    def _handle_data_packet(self, packet, sensor, mqtt_json):
        # radio packet of proper rorg type received; parse EEP
        found_property = False
        direction = None
        if sensor.get('direction'):
            direction = sensor.get('direction')

        # Retrieve command from the received packet and pass it to parse_eep()
        command = None
        if sensor.get('command'):
            command = self._get_command_id(packet, sensor)
            if command:
                logging.debug('Retrieved command id from packet: %s', hex(command))

        # Retrieve properties from EEP
        properties = packet.parse_eep(sensor['func'], sensor['type'], direction, command)

        # Now send also raw data to MQTT
        raw_data = packet.data[1:len(packet.data)-1-4]
        raw_data.append(packet.data[-1])
        mqtt_json["_RAW_DATA_"] = enocean.utils.to_hex_string(raw_data)

        # loop through all EEP properties
        for prop_name in properties:
            found_property = True
            cur_prop = packet.parsed[prop_name]
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

    #=============================================================================================
    # LOW LEVEL FUNCTIONS
    #=============================================================================================
    def _reply_packet(self, in_packet, sensor):
        '''send enocean message as a reply to an incoming message'''
        # prepare addresses
        destination = in_packet.sender

        self._send_packet(sensor, destination, None, True,
                          in_packet.data if in_packet.learn else None)

    # ------------------------------------------------------------------------
    # Bi-directional / smartACK support
    # ------------------------------------------------------------------------
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
            sender = [(sensor['sender'] >> i * 8) & 0xff for i in reversed(range(4))]
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
            sender = [(sensor['sender'] >> i * 8) & 0xff for i in reversed(range(4))]
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
            sender = [(sensor['sender'] >> i*8) & 0xff for i in reversed(range(4))]
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
                        packet.data[1:5] = [(default_data >> i*8) &
                                        0xff for i in reversed(range(4))]
                    except:
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
        self.enocean.send(packet)

    def _send_teachin(self, name):
        """send a teach-in telegram to an actor so it learns this gateway as
        its controller.

        Works for 4BS actors (LRN bit set) and VLD actors (VLD teach-in
        packet). Returns (ok, message).
        """
        sensor = None
        for s in self.sensors:
            if s.get('name') == name or s.get('name') == self.conf.get('mqtt_prefix', 'enocean/') + name:
                sensor = s
                break
        if sensor is None:
            return False, 'Device not found'

        rorg = sensor.get('rorg')
        func = sensor.get('func')
        type_ = sensor.get('type')
        if rorg not in (RORG.BS4, RORG.VLD):
            return False, 'Teach-in telegram only supported for 4BS and VLD actors'

        # teach-in makes sense for actors and bi-directional devices (they can
        # receive and register the gateway). Plain one-way sensors cannot.
        # Resolve the category from the EEP registry if not stored on the dict.
        category = sensor.get('category')
        bidirectional = sensor.get('bidirectional')
        if category is None or bidirectional is None:
            profile = self._eep_registry.get(rorg, func, type_) if type_ is not None else None
            if profile:
                category = profile.get('category', 'sensor') if category is None else category
                bidirectional = profile.get('bidirectional', False) if bidirectional is None else bidirectional
        is_actor = category == 'actor' or bidirectional
        if not is_actor:
            return False, 'Teach-in telegram is only supported for actors / bidirectional devices'

        address = sensor.get('address')
        destination = [(address >> i * 8) & 0xff for i in reversed(range(4))] if address is not None else None

        try:
            if rorg == RORG.VLD:
                # VLD teach-in: build a UTE-style packet targeting the actor
                packet = RadioPacket.create(RORG.VLD, func, type_,
                                            sender=self.enocean_sender,
                                            destination=destination,
                                            learn=True)
                self.enocean.send(packet)
            else:
                # 4BS teach-in: set the LRN bit in DB0 (data[1] bit 3)
                packet = RadioPacket.create(RORG.BS4, func, type_,
                                            sender=self.enocean_sender,
                                            destination=destination,
                                            learn=True)
                # force the learn bit (LRN=1) in DB0
                if len(packet.data) >= 2:
                    packet.data[1] |= 0x08
                    packet.parse_eep(func, type_)
                self.enocean.send(packet)
            logging.info("Teach-in telegram sent to %s (%s)", sensor['name'],
                         enocean.utils.to_hex_string(destination) if destination else 'broadcast')
            return True, 'Teach-in telegram sent'
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Failed to send teach-in to %s: %s", sensor['name'], exc)
            return False, str(exc)

    def _process_radio_packet(self, packet):
        # Universal Teach-In telegrams are handled separately
        if packet.rorg == RORG.UTE:
            self._handle_ute_packet(packet)
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
        # Only a genuine teach-in is added - the user must have pressed the
        # teach-in button, which is visible as the learn bit in the telegram.
        #  - UTE (0xD4) is always a teach-in telegram.
        #  - 4BS (0xA5) / 1BS (0xD5) carry the learn bit (LRN) in DB0 bit 3;
        #    we only add them when it is set.
        #  - RPS (0xF6) has no learn bit - the teach-in is the (repeated)
        #    button/data telegram itself, so a device is captured when it
        #    sends any telegram while learn mode is on.
        #  - VLD (0xD2) devices use UTE (0xD4) as their teach-in mechanism -
        #    a regular VLD data telegram is NOT a teach-in and must NOT be
        #    auto-captured (only UTE or a manual add registers them).
        #  - Anything else (e.g. a 4BS/1BS data telegram without the learn
        #    bit, a VLD data telegram) is NOT added - the teach-in button was
        #    not pressed.
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
            logging.info("unknown sensor: %s (RORG = %s)", enocean.utils.to_hex_string(packet.sender), hex(packet.rorg))
            return

        # track last-seen for the web interface
        self._last_seen[address] = datetime.datetime.utcnow()

        # Handling EnOcean library decision to set learn to True by default.
        # Only 1BS and 4BS are correctly handled by the EnOcean library.
        # -> VLD EnOcean devices use UTE as learn mechanism
        if found_sensor['rorg'] == RORG.VLD and packet.rorg != RORG.UTE:
            packet.learn = False
        # -> RPS EnOcean devices only send normal data telegrams.
        # Hence learn can always be set to false
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


    #=============================================================================================
    # RUN LOOP
    #=============================================================================================
    def run(self):
        """the main loop with blocking enocean packet receive handler"""
        # start endless loop for listening
        while self.enocean.is_alive():
            # Request transmitter ID, if needed
            if self.enocean_sender is None:
                self.enocean_sender = self.enocean.base_id

            # Loop to empty the queue...
            try:
                # get next packet
                packet = self.enocean.receive.get(block=True, timeout=1)

                # check packet type
                if packet.packet_type == PACKET.RADIO:
                    self._process_radio_packet(packet)
                elif packet.packet_type == PACKET.RESPONSE:
                    response_code = RETURN_CODE(packet.data[0])
                    logging.info("got response packet: %s", response_code.name)
                else:
                    logging.info("got non-RF packet: %s", packet)
                    continue
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                logging.debug("Exception: KeyboardInterrupt")
                break

        # Run finished, close MQTT client and stop Enocean thread
        logging.debug("Cleaning up")
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        self.mqtt.loop_forever()  # will block until disconnect complete
        self.enocean.stop()

# Author: Marc Alexandre K. <marcalexandrek-developer@yahoo.fr>
"""EnOcean Equipment Profile (EEP) registry

The Python enocean library ships an ``EEP.xml`` file (the same database that is
published as the EnOcean EEP PDF) describing every supported RORG/FUNC/TYPE
profile. This module reads that file and exposes the list of profiles so the web
interface can offer a human-readable EEP picker (e.g. when adding a sensor
manually or when a UTE teach-in telegram does not carry the EEP).

The enocean package must be importable (it is a hard dependency of enoceanmqtt).
"""
import logging
import os
import xml.etree.ElementTree as ET

#: short human names for the EnOcean RORG families
RORG_NAMES = {
    '0xF6': 'RPS (Switches / Rocker)',
    '0xD5': '1BS (Contact / Status)',
    '0xA5': '4BS (Measurement)',
    '0xD2': 'VLD (Variable Length)',
    '0xD1': 'MSC (Managed / Repeater)',
}

#: explicit overrides for profiles that are not (or are ambiguous) in the
#: EEP database. Keyed by 'RORG-FUNC-TYPE'. ``category`` is one of
#: ``sensor``, ``actor`` or ``bidirectional``; ``smartack`` marks devices
#: that expect a fast (<~300ms) acknowledgement reply.
#:
#: A5-20-01 - 4BS "Battery Powered Actuator" family, bi-directional actor
#:            (the EEP database names func 0x20 "Actuator (BI-DIR)").
#: D2-11-01 - VLD smartACK "Bidirectional valve" profile. Not present in the
#:            shipped EEP.xml (v2.6.4), added here so it can be used and gets
#:            the fast smartACK reply path.
EEP_OVERRIDES = {
    'A5-20-01': {'category': 'bidirectional', 'name': 'Bi-directional Battery Powered Actuator'},
    'A5-20-02': {'category': 'bidirectional', 'name': 'Bi-directional Battery Powered Actuator'},
    'A5-20-03': {'category': 'bidirectional', 'name': 'Bi-directional Battery Powered Actuator'},
    'A5-20-09': {'category': 'bidirectional', 'name': 'Bi-directional Battery Powered Actuator'},
    'A5-20-0A': {'category': 'bidirectional', 'name': 'Bi-directional Battery Powered Actuator'},
    'D2-11-01': {'category': 'sensor', 'smartack': True,
                 'name': 'Bidirectional Valve (smartACK)', 'rorg_name': 'VLD (Variable Length)'},
    'D2-11-02': {'category': 'sensor', 'smartack': True,
                 'name': 'Bidirectional Valve (smartACK)', 'rorg_name': 'VLD (Variable Length)'},
}

#: RORG-FUNC families that are actors (devices that RECEIVE commands from us,
#: as opposed to sensors that send measurements). Actors may still report
#: status back, hence some are also bidirectional.
ACTOR_FUNCS = {
    0xA5: {0x10, 0x11, 0x20},
    0xD2: {0x01, 0x03, 0x05, 0x06},
}

#: RORG-FUNC families that are inherently bidirectional (device expects a
#: reply from the controller when it sends a telegram).
BIDIRECTIONAL_FUNCS = {
    0xA5: {0x20},
    0xD2: {0x01, 0x06, 0x11},
}

#: RORG-FUNC families that use smartACK (fast acknowledgement required).
SMARTACK_FUNCS = {
    0xD2: {0x11},
}


class EEPRegistry:
    """reads the enocean library EEP database into a simple list of profiles"""

    def __init__(self):
        self.profiles = []
        self._rorg_names = {}
        try:
            self._load()
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Cannot load EEP registry: %s", exc)

    def _load(self):
        # locate EEP.xml shipped with the enocean package
        try:
            import enocean
        except ImportError:
            logging.error("enocean package not available, EEP registry is empty")
            return
        eep_path = os.path.join(os.path.dirname(os.path.realpath(enocean.__file__)),
                                'protocol', 'EEP.xml')
        if not os.path.isfile(eep_path):
            logging.error("Cannot find EEP.xml at %s", eep_path)
            return
        tree = ET.parse(eep_path)
        root = tree.getroot()
        for telegram in root.findall('telegram'):
            rorg = telegram.get('rorg')
            rorg_name = RORG_NAMES.get(rorg, telegram.get('type', ''))
            for function in telegram.findall('profiles'):
                func = function.get('func')
                for profile in function.findall('profile'):
                    type_ = profile.get('type')
                    entry = {
                        'rorg': int(rorg, 16),
                        'func': int(func, 16),
                        'type': int(type_, 16),
                        'rorg_hex': rorg,
                        'func_hex': func,
                        'type_hex': type_,
                        'eep': f'{rorg}-{func}-{type_}',
                        'name': profile.get('description') or f'Type {type_}',
                        'rorg_name': rorg_name,
                    }
                    self._classify(entry)
                    self.profiles.append(entry)

        # add override profiles that are not present in the EEP.xml database
        existing = {(p['rorg'], p['func'], p['type']) for p in self.profiles}
        for eep, override in EEP_OVERRIDES.items():
            try:
                rorg, func, type_ = (int(x, 16) for x in eep.split('-'))
            except (ValueError, TypeError):
                continue
            if (rorg, func, type_) in existing:
                continue
            rorg_hex = f'0x{rorg:02X}'
            func_hex = f'0x{func:02X}'
            type_hex = f'0x{type_:02X}'
            smartack = bool(override.get('smartack'))
            # smartACK devices are inherently bidirectional
            bidirectional = (override.get('category') == 'bidirectional') or smartack
            category = override.get('category', 'sensor')
            if bidirectional:
                category = 'bidirectional'
            entry = {
                'rorg': rorg, 'func': func, 'type': type_,
                'rorg_hex': rorg_hex, 'func_hex': func_hex, 'type_hex': type_hex,
                'eep': f'{rorg_hex}-{func_hex}-{type_hex}',
                'name': override.get('name', f'Type {type_:02X}'),
                'rorg_name': override.get('rorg_name', 'VLD (Variable Length)'),
                'category': category,
                'bidirectional': bidirectional,
                'smartack': smartack,
            }
            self.profiles.append(entry)

    @staticmethod
    def _classify(entry):
        '''assign category / bidirectional / smartack flags to a profile'''
        eep = entry['eep']
        rorg = entry['rorg']
        func = entry['func']
        override = EEP_OVERRIDES.get(eep)
        if override:
            entry['category'] = override.get('category', 'sensor')
            entry['bidirectional'] = bool(override.get('category') == 'bidirectional' or
                                          override.get('bidirectional'))
            entry['smartack'] = bool(override.get('smartack'))
            if 'name' in override:
                entry['name'] = override['name']
            if 'rorg_name' in override:
                entry['rorg_name'] = override['rorg_name']
            return

        bidirectional = func in BIDIRECTIONAL_FUNCS.get(rorg, set())
        smartack = func in SMARTACK_FUNCS.get(rorg, set())
        is_actor = func in ACTOR_FUNCS.get(rorg, set())

        # smartACK devices are inherently bidirectional (they receive commands
        # and report status via the smartACK handshake), e.g. D2-11-01 - just
        # a different mechanism than the A5-20 bidirectional family.
        if smartack:
            bidirectional = True

        # A bidirectional device (smartACK or A5-20 ...) is its own third
        # category. A plain one-way actor (receiver) stays an actor.
        if bidirectional:
            category = 'bidirectional'
        elif is_actor:
            category = 'actor'
        else:
            category = 'sensor'
        entry['category'] = category
        entry['bidirectional'] = bidirectional
        entry['smartack'] = smartack

    def search(self, query=''):
        """return profiles whose name/EEP matches the query (case-insensitive)"""
        query = (query or '').strip().lower()
        if not query:
            return list(self.profiles)
        return [p for p in self.profiles
                if query in p['name'].lower() or query in p['eep'].lower()]

    def get(self, rorg, func, type_):
        """return a single profile by numeric rorg/func/type, or None"""
        for p in self.profiles:
            if p['rorg'] == rorg and p['func'] == func and p['type'] == type_:
                return p
        return None

    def default_for_rorg(self, rorg):
        """pick a sensible default profile for a RORG when the EEP is not
        known (e.g. an RPS/F6 switch or 4BS sensor that did not send a learn
        telegram). Prefers the first-listed profile for that RORG."""
        for p in self.profiles:
            if p['rorg'] == rorg:
                return p
        return None


# module level singleton
_registry = None


def get_registry():
    """return a cached EEPRegistry singleton"""
    global _registry   # pylint: disable=global-statement
    if _registry is None:
        _registry = EEPRegistry()
    return _registry

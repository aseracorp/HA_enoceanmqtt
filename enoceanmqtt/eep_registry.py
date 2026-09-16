"""EnOcean Equipment Profile (EEP) registry — backed by the code-defined engine.

This replaces the old EEP.xml parser. Profiles are now expressed as *code* in
``eep_engine/`` (the same engine that validates against the official EnOcean
certification vectors — see ``tests/test_eep_certification.py``). There is no
hardcoded EEP table here.

The web UI needs each profile tagged with a category so the Add popup can
offer sensors / actors / bidirectional devices. Per the project design, every
profile is usable as a sensor OR as an actor (we can simulate any sensor EEP
as a virtual sender), so profiles are only split into:
  - ``bidirectional`` — the handful of EEP families that genuinely send AND
    receive (e.g. A5-20 actuators, D2-01/06 smartACK valves, D2-11 smartACK)
  - ``sensor`` — everything else (also usable as an actor).
``smartack`` marks the fast-acknowledgement devices that expect a quick reply.
"""
import logging

from enoceanmqtt.eep_engine import PROFILES

#: RORG-FUNC families that are inherently bidirectional (device sends a
#: telegram and expects a reply from the controller).
BIDIRECTIONAL_FUNCS = {
    0xA5: {0x20},
    0xD2: {0x01, 0x06, 0x11},
}

#: RORG-FUNC families that use smartACK (fast acknowledgement required).
SMARTACK_FUNCS = {
    0xD2: {0x11},
}


class EEPRegistry:
    """the code-defined EEP profiles, exposed as the legacy list API."""

    def __init__(self):
        self.profiles = []
        for (rorg, func, type_), profile in PROFILES.items():
            entry = {
                'rorg': rorg,
                'func': func,
                'type': type_,
                'rorg_hex': f'0x{rorg:02X}',
                'func_hex': f'0x{func:02X}',
                'type_hex': f'0x{type_:02X}',
                'eep': f'0x{rorg:02X}-0x{func:02X}-0x{type_:02X}',
                'name': profile.title,
                'rorg_name': self._rorg_name(rorg),
            }
            self._classify(entry)
            self.profiles.append(entry)
        self.profiles.sort(key=lambda p: (p['rorg'], p['func'], p['type']))

    @staticmethod
    def _rorg_name(rorg):
        names = {
            0xF6: 'RPS (Switches / Rocker)',
            0xD5: '1BS (Contact / Status)',
            0xA5: '4BS (Measurement)',
            0xD2: 'VLD (Variable Length)',
            0xD1: 'MSC (Managed / Repeater)',
        }
        return names.get(rorg, f'RORG 0x{rorg:02X}')

    @staticmethod
    def _classify(entry):
        """assign category / bidirectional / smartack flags to a profile."""
        rorg = entry['rorg']
        func = entry['func']
        smartack = func in SMARTACK_FUNCS.get(rorg, set())
        bidirectional = func in BIDIRECTIONAL_FUNCS.get(rorg, set()) or smartack
        entry['smartack'] = smartack
        entry['bidirectional'] = bidirectional
        # every profile is usable as a sensor or an actor; only the
        # bidirectional families form their own third category.
        entry['category'] = 'bidirectional' if bidirectional else 'sensor'

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

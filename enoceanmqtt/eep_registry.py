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
                    self.profiles.append({
                        'rorg': int(rorg, 16),
                        'func': int(func, 16),
                        'type': int(type_, 16),
                        'rorg_hex': rorg,
                        'func_hex': func,
                        'type_hex': type_,
                        'eep': f'{rorg}-{func}-{type_}',
                        'name': profile.get('description') or f'Type {type_}',
                        'rorg_name': rorg_name,
                    })

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


# module level singleton
_registry = None


def get_registry():
    """return a cached EEPRegistry singleton"""
    global _registry   # pylint: disable=global-statement
    if _registry is None:
        _registry = EEPRegistry()
    return _registry

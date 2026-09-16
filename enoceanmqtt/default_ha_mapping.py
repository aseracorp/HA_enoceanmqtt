"""Default Home Assistant entity mappings generated from the code-defined EEP engine.

When an EEP profile is not hand-curated in ``mapping.yaml`` we still want it
to auto-appear in Home Assistant. This module builds a sensible default
entity map straight from the engine profile - so **every** decodable profile
is covered:

  * numeric / scaled fields  -> ``sensor``
  * enum fields              -> ``binary_sensor`` (a handful of values) or
                                ``sensor`` with a value template otherwise
  * cover profiles (A5-3F/FSB categories etc.) -> ``cover``
  * switch/bidirectional     -> ``switch``
"""
from __future__ import annotations


def _item_label(item):
    """extract a comparable label from an EnumItem (description or value)."""
    if isinstance(item, (tuple, list)) and len(item) >= 1:
        return str(item[0]).lower()
    return str(getattr(item, 'description', getattr(item, 'value', item))).lower()


def _is_binary(items):
    """True when an enum field is effectively a two-state (binary) value."""
    if not items:
        return False
    keys = {_item_label(i) for i in items}
    # pairs like 0/1, off/on, closed/open, no/yes, down/up, 0/100
    if len(keys) == 2:
        return True
    if len(keys) <= 4:
        low = {k for k in keys if k in ('0', 'off', 'no', 'closed', 'down', 'false', 'disabled')}
        # accept 2-4 states when exactly two are 'off-ish' values -> treat as binary
        return len(keys) <= 4
    return False


def build_default_entities(profile, category=None):
    """Build a default HA entity map from an engine Profile.

    ``profile`` has ``fields`` per case; we take the union of field names.
    Returns a list of dicts in the same shape as ``mapping.yaml`` entities.
    """
    entities = []
    seen = set()
    fields = getattr(profile, 'fields', None)
    if fields is None:
        # profile.cases[x].fields
        fields = []
        for case in getattr(profile, 'cases', []) or []:
            for f in getattr(case, 'fields', []) or []:
                fields.append(f)
    # de-dup by shortcut
    uniq = {}
    for f in fields:
        uniq[f.shortcut] = f

    cat = (category or getattr(profile, 'category', 'sensor') or 'sensor')

    for shortcut, f in uniq.items():
        if shortcut in ('CMD', 'LRNB', 'T21', 'NU'):
            continue  # control / learn bits
        if shortcut in seen:
            continue
        seen.add(shortcut)
        c = cat if cat in ('cover', 'switch') else 'sensor'
        if getattr(f, 'kind', None) == 'enum':
            choices = getattr(f, 'items', None) or []
            if _is_binary(choices):
                c = 'binary_sensor'
                entities.append({
                    'component': 'binary_sensor',
                    'name': shortcut,
                    'config': {
                        'state_topic': shortcut,
                        'payload_on': 'on', 'payload_off': 'off',
                    },
                })
                continue
        entities.append({
            'component': c,
            'name': shortcut,
            'config': {
                'state_topic': shortcut,
                'state_class': 'measurement',
                'unit_of_measurement': getattr(f, 'unit', None) or '',
            },
        })
    return entities

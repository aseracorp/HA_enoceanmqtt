# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""Decode/encode engine over the code-defined EEP profiles (`PROFILES`).

Case selection is done by evaluating each case's condition bit-predicates (and optional direction)
against the actual telegram bits, so the received data itself picks the layout — the same mechanism
the certification vectors assume.
"""

from __future__ import annotations

from collections import OrderedDict

from enoceanmqtt.eep_engine import PROFILES
from enoceanmqtt.eep_engine._model import Case, Field, Profile


def find_profile(rorg: int, func: int, type_: int) -> Profile | None:
    return PROFILES.get((rorg, func, type_))


def _raw(bits, offset, size):
    """Integer value of bits[offset:offset+size] (bits is a list of bool/0/1)."""
    if offset + size > len(bits):
        return None
    return int("".join("1" if b else "0" for b in bits[offset : offset + size]) or "0", 2)


def _condition_holds(cond, bit_data, bit_status) -> bool:
    bits = bit_status if cond.source == "status" else bit_data
    return _raw(bits, cond.offset, cond.size) == cond.value


def select_case(
    profile: Profile, bit_data, bit_status, direction=None, command=None
) -> Case | None:
    """Select a profile's data layout.

    For decode the received bits themselves satisfy each case's ``<condition>`` predicates. For
    encode (bits not yet set) an explicit ``command`` picks the case whose command-field condition
    equals it (or the unconditioned default case). ``direction`` narrows bidirectional profiles.
    """
    fallback = None
    for case in profile.cases:
        if case.direction is not None and direction is not None and case.direction != direction:
            continue
        # Explicit command selection (encode path): match the case's data-field condition value.
        if command is not None and case.conditions:
            data_conds = [c for c in case.conditions if c.source == "data"]
            if data_conds and all(c.value == command for c in data_conds):
                return case
            continue
        if all(_condition_holds(c, bit_data, bit_status) for c in case.conditions):
            if case.conditions or case.direction is not None:
                return case
            fallback = fallback or case  # unconditional case = last resort / default command
    return fallback if fallback is not None else (profile.cases[0] if profile.cases else None)


def _decode_field(field: Field, bits) -> dict | None:
    raw = _raw(bits, field.offset, field.size)
    if raw is None:
        return None
    common = {"description": field.name, "unit": field.unit, "raw_value": raw}

    if field.kind == "value":
        rmin, rmax = field.range_min, field.range_max
        smin, smax = field.scale_min, field.scale_max
        if rmin is None or rmax is None or smin is None or smax is None or rmax == rmin:
            return {**common, "value": raw}
        value = (smax - smin) / (rmax - rmin) * (raw - rmin) + smin
        return {**common, "value": value}

    if field.kind == "enum":
        return {**common, "value": _enum_value(field, raw)}

    if field.kind == "bool":
        return {**common, "value": bool(raw)}

    # raw / fixed
    return {**common, "value": raw}


def _enum_value(field: Field, raw: int):
    for item in field.items:
        if item.value is not None and item.value == raw:
            return _fmt(item.description, raw)
        if item.min is not None and item.max is not None and item.min <= raw <= item.max:
            if item.scale_min is not None and item.scale_max is not None and item.max != item.min:
                return (item.scale_max - item.scale_min) / (item.max - item.min) * (
                    raw - item.min
                ) + item.scale_min
            return _fmt(item.description, raw)
    return raw  # no matching enum item — expose the raw value


def _fmt(description: str, raw: int) -> str:
    """Substitute the ``{value}`` placeholder some range-item descriptions use (e.g. VLD 'Output
    value {value}%') via ``description.format(value=raw)``."""
    if "{value}" not in description:
        return description
    try:
        return description.format(value=raw)
    except (KeyError, IndexError, ValueError):
        return description


def decode(case: Case, bit_data, bit_status) -> OrderedDict:
    """Decode all shortcut-bearing data + status fields of *case* to {shortcut: {...}}."""
    out: OrderedDict = OrderedDict()
    for field in case.fields:
        if not field.shortcut:
            continue
        decoded = _decode_field(field, bit_data)
        if decoded is not None:
            out[field.shortcut] = decoded
    for field in case.status_fields:
        if not field.shortcut:
            continue
        decoded = _decode_field(field, bit_status)
        if decoded is not None:
            out[field.shortcut] = decoded
    return out


# --- encode (inverse of decode) ---------------------------------------------------------------
# Value fields use int() truncation of the inverse linear scale, enums accept the raw int (if it
# maps to an item/range) or a description string, and booleans set a single bit. This is the runtime
# encode/decode core; RadioPacket.create/parse_eep delegate here.


def _set_raw(bits, offset, size, raw) -> None:
    """Write *raw* into bits[offset:offset+size], MSB first."""
    for digit in range(size):
        bits[offset + digit] = (raw >> (size - digit - 1)) & 0x01 != 0


def _encode_value(field: Field, value) -> int:
    rmin, rmax, smin, smax = field.range_min, field.range_max, field.scale_min, field.scale_max
    if rmin is None or rmax is None or smin is None or smax is None or smax == smin:
        return int(float(value))
    return int((float(value) - smin) * (rmax - rmin) / (smax - smin) + rmin)


def _enum_has_raw(field: Field, raw: int) -> bool:
    """True if *raw* is a valid encoding for the enum (a discrete item value or within a range)."""
    for item in field.items:
        if item.value is not None and item.value == raw:
            return True
        if item.min is not None and item.max is not None and item.min <= raw <= item.max:
            return True
    return False


def _encode_enum(field: Field, value) -> int:
    """Raw for an enum. An int (or int-like string) must match an item value or a range (commands,
    setpoints and levels are ranges); a string is matched against the item descriptions. Rejects an
    out-of-range value (guards against illegal enum inputs)."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        value = int(value)
    if isinstance(value, int):
        if _enum_has_raw(field, value):
            return value
        raise ValueError(f'Enum value "{value}" not valid for {field.shortcut}')
    for item in field.items:
        if item.value is not None and item.description == value:
            return item.value
    raise ValueError(f'Enum description "{value}" not found for {field.shortcut}')


def _encode_field(field: Field, value) -> int:
    if field.kind == "value":
        return _encode_value(field, value)
    if field.kind == "enum":
        return _encode_enum(field, value)
    if field.kind == "bool":
        return 1 if (value is True or str(value) in ("1", "True", "true")) else 0
    if field.kind == "fixed":
        return field.fixed_value or 0
    return int(value)  # raw


def encode(case: Case, properties: dict, bit_data, bit_status):
    """Set *properties* ({shortcut: value}) into the bit arrays, returning them updated.

    Unknown shortcuts are skipped. Callers pass mutable bit lists (typically the packet's
    zero-initialised ``_bit_data``/``_bit_status``).
    """
    data_fields = {f.shortcut: f for f in case.fields if f.shortcut}
    status_fields = {f.shortcut: f for f in case.status_fields if f.shortcut}
    for shortcut, value in properties.items():
        if shortcut in data_fields:
            field = data_fields[shortcut]
            _set_raw(bit_data, field.offset, field.size, _encode_field(field, value))
        elif shortcut in status_fields:
            field = status_fields[shortcut]
            _set_raw(bit_status, field.offset, field.size, _encode_field(field, value))
    return bit_data, bit_status

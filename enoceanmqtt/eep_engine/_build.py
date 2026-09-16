# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""Builder helpers for the EEP profile catalog (consumed by the ``eep/`` fragments).

Factories that default the rarely-used kwargs, so each profile states only what differs — the data
files carry meaning, not `range_min=None, …, fixed_value=None` noise. They return the frozen
``_model`` dataclasses; sharing an immutable ``Field``/``EnumItem`` across profiles is safe. The
assembled ``PROFILES`` is checked by ``tests/protocol/test_profiles.py``.
"""

from __future__ import annotations

from collections.abc import Iterable

from enoceanmqtt.eep_engine._model import Case, Condition, EnumItem, Field, Profile


def enum(
    description: str,
    value: int | None = None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    scale_min: float | None = None,
    scale_max: float | None = None,
    unit: str = "",
) -> EnumItem:
    """One enum entry — a discrete ``value`` or an inclusive ``[minimum, maximum]`` range."""
    return EnumItem(
        description=description,
        value=value,
        min=minimum,
        max=maximum,
        scale_min=scale_min,
        scale_max=scale_max,
        unit=unit,
    )


def field(
    shortcut: str,
    name: str,
    offset: int,
    size: int,
    kind: str,
    *,
    unit: str = "",
    range_min: float | None = None,
    range_max: float | None = None,
    scale_min: float | None = None,
    scale_max: float | None = None,
    items: Iterable[EnumItem] = (),
    fixed_value: int | None = None,
) -> Field:
    """A data/status field; only the provided optional attributes are set."""
    return Field(
        shortcut=shortcut,
        name=name,
        offset=offset,
        size=size,
        kind=kind,
        unit=unit,
        range_min=range_min,
        range_max=range_max,
        scale_min=scale_min,
        scale_max=scale_max,
        items=tuple(items),
        fixed_value=fixed_value,
    )


def cond(source: str, offset: int, size: int, value: int) -> Condition:
    """A bit-predicate that selects a case (field at offset/size must equal ``value``)."""
    return Condition(source=source, offset=offset, size=size, value=value)


def case(
    fields: Iterable[Field] = (),
    *,
    conditions: Iterable[Condition] = (),
    direction: int | None = None,
    status_fields: Iterable[Field] = (),
) -> Case:
    """One conditional data layout of a profile."""
    return Case(
        conditions=tuple(conditions),
        direction=direction,
        fields=tuple(fields),
        status_fields=tuple(status_fields),
    )


def profile(rorg: int, func: int, typ: int, title: str, *cases: Case) -> Profile:
    """A full EEP profile (one or more cases)."""
    return Profile(rorg=rorg, func=func, type=typ, title=title, cases=tuple(cases))


# Verbatim-repeated across (nearly all) 4BS profiles: the teach-in LRN bit at DB0.3. Shared as one
# immutable instance (the DRY analog of the mapping catalog).
LRNB_4BS: Field = field(
    "LRNB",
    "LRN Bit",
    28,
    1,
    "enum",
    items=(enum("Teach-in telegram", 0), enum("Data telegram", 1)),
)

__all__: list[str] = ["LRNB_4BS", "case", "cond", "enum", "field", "profile"]

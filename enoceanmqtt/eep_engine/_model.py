# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""Typed model for EEP profiles expressed as code.

These dataclasses shape the ``PROFILES`` catalog in ``profiles.py``. The decode/encode engine
consumes this model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EnumItem:
    """One enum entry: either a discrete ``value`` or an inclusive ``[min, max]`` range."""

    description: str
    value: int | None = None
    min: int | None = None
    max: int | None = None
    # a range item may itself scale raw->physical within [min, max]
    scale_min: float | None = None
    scale_max: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class Field:
    """A data (or status) field at a fixed bit position within the telegram."""

    shortcut: str
    name: str
    offset: int
    size: int
    kind: str  # 'value' | 'enum' | 'bool' | 'raw' | 'fixed'
    unit: str = ""
    range_min: float | None = None
    range_max: float | None = None
    scale_min: float | None = None
    scale_max: float | None = None
    items: tuple[EnumItem, ...] = ()
    fixed_value: int | None = None  # for kind == 'fixed'


@dataclass(frozen=True)
class Condition:
    """A bit-predicate selecting a case: field at (offset,size) must equal ``value``."""

    source: str  # 'data' | 'status'
    offset: int
    size: int
    value: int


@dataclass(frozen=True)
class Case:
    """One conditional data layout of a profile (chosen by conditions + direction)."""

    conditions: tuple[Condition, ...] = ()
    direction: int | None = None
    fields: tuple[Field, ...] = ()
    status_fields: tuple[Field, ...] = ()


@dataclass(frozen=True)
class Profile:
    rorg: int
    func: int
    type: int
    title: str
    cases: tuple[Case, ...] = field(default_factory=tuple)

    @property
    def eep(self) -> str:
        return f"{self.rorg:02X}-{self.func:02X}-{self.type:02X}"

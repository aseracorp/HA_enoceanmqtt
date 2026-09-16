# Code-defined EEP engine

This directory is a **code-defined EnOcean EEP engine** - there are **no
hardcoded EEP tables in config files**. Every one of the 230 equipment
profiles is expressed as **Python code** (`eep/*.py`): bit-field layouts,
value scales, enum/condition mappings, and profile titles.

## Design

- `_model.py` - the profile/case/field data model (frozen dataclasses).
- `_build.py` - small builder helpers to declare profiles compactly.
- `engine.py` - decode/encode + case selection (stateless, bit-driven).
- `eep/*.py` - the profiles themselves, grouped by RORG family
  (A5 = 4BS sensors/actuators, D2 = VLD devices, F6 = RPS switches,
  D5 = 1BS Contacts).
- `utils.py` - bit/payload helpers (stdlib-only).

No runtime dependency on any EEP XML or JSON configuration. The engine is
self-contained (stdlib only).

## Certification validation (test-only)

`tests/fixtures/certification/eep_certification.json` is the **official
EnOcean certification vector corpus** - raw telegram bytes with their
expected decoded values. It is used **only by tests**
(`tests/test_eep_certification.py`) to prove the code-defined engine
reproduces the certified decode outputs (>= 95% pass rate). It is **not**
read by the runtime and does not define any EEP.

Ported from [t-ice/enocean-mqtt-ha](https://github.com/t-ice/enocean-mqtt-ha)
(GPL-3.0, same license).

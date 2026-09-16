# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""EEP profiles as code.

``PROFILES`` is the single code-defined catalog of EnOcean Equipment Profiles, keyed by
``(rorg, func, type)``. It is split into per-family fragments under ``eep/`` for readability and
merged here. Value is checked by ``tests/protocol/test_profiles.py`` and the certification vectors.
"""

from enoceanmqtt.eep_engine._model import (
    Case,
    Condition,
    EnumItem,
    Field,
    Profile,
)
from enoceanmqtt.eep_engine.eep import (
    a5_hvac,
    a5_room_panels,
    a5_sensors,
    a5_weather,
    d2_devices,
    d2_switches,
    f6,
)

# The catalog, assembled from the per-family fragments (keys are globally unique).
PROFILES: dict[tuple[int, int, int], Profile] = {}
for _frag in (
    a5_sensors,
    a5_room_panels,
    a5_hvac,
    a5_weather,
    d2_switches,
    d2_devices,
    f6,
):
    PROFILES.update(_frag.PROFILES)


class ProfileRegistry:
    """Lookup of EEP profiles by ``(rorg, func, type)``.

    A thin, injectable interface over the profile table: the decode/encode codec depends on this
    small surface rather than reaching for the module-level ``PROFILES`` dict, so a test (or an
    alternate profile set) can be supplied as a constructor argument instead of monkeypatching.
    """

    def __init__(self, profiles: dict[tuple[int, int, int], Profile]):
        self._profiles = profiles

    def find(self, rorg: int, func: int, type_: int) -> Profile | None:
        return self._profiles.get((rorg, func, type_))

    def __contains__(self, key: object) -> bool:
        return key in self._profiles

    def __len__(self) -> int:
        return len(self._profiles)


# The default registry over the code-defined profiles (the codec uses this unless one is injected).
DEFAULT_PROFILE_REGISTRY = ProfileRegistry(PROFILES)

__all__ = [
    "DEFAULT_PROFILE_REGISTRY",
    "PROFILES",
    "Case",
    "Condition",
    "EnumItem",
    "Field",
    "Profile",
    "ProfileRegistry",
]

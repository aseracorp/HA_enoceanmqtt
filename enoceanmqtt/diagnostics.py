"""Transceiver diagnostics (ESP3 common commands + F2 events).

Reads controller identity / health from the EnOcean stick:
  - chip id, app version, api version   (CO_RD_VERSION, 0x03)
  - repeater level                       (CO_RD_REPEATER, 0x0A)
  - available TX duty-cycle %            (CO_RD_DUTYCYCLE_LIMIT, 0x23)
  - transmit-failure counter             (F2 CO_TRANSMIT_FAILED events)

Command numbers are the ESP3 COMMON_COMMAND codes from the EnOcean Serial
Protocol 3 specification; response layouts follow the spec:
  CO_RD_VERSION     -> app(4) api(4) chip_id(4) chip_ver(4) ...
  CO_RD_REPEATER    -> REP_ENABLE(1) REP_LEVEL(1)
  CO_RD_DUTYCYCLE_LIMIT -> AVAILABLE(1, percent) ...

Adapted from t-ice/enocean-mqtt-ha `application/daemon.py` (GPL-3.0).
"""
from __future__ import annotations

# ESP3 COMMON_COMMAND codes (see ESP3 spec / enocean lib constants)
CO_RD_VERSION = 0x03
CO_RD_REPEATER = 0x0A
CO_RD_DUTYCYCLE_LIMIT = 0x23

# F2 EVENT codes (from the transceiver, delivered as ESP3 EVENT packets)
EV_DUTYCYCLE_LIMIT = 0x02
EV_TRANSMIT_FAILED = 0x03


def parse_version(response_data):
    """Parse a CO_RD_VERSION response_data into (app, api, chip_id_hex)."""
    if len(response_data) < 16:
        return None, None, None
    app = ".".join(str(b) for b in response_data[0:4])
    api = ".".join(str(b) for b in response_data[4:8])
    chip = ":".join("%02X" % b for b in response_data[8:12])
    return app, api, chip


def parse_repeater(response_data):
    """CO_RD_REPEATER -> repeater level (0=off, 1 or 2) or None."""
    if len(response_data) < 2:
        return None
    enable, level = response_data[0], response_data[1]
    return 0 if enable == 0 else level


def parse_duty_cycle(response_data):
    """CO_RD_DUTYCYCLE_LIMIT -> available TX duty-cycle percent or None."""
    if not response_data:
        return None
    return response_data[0]

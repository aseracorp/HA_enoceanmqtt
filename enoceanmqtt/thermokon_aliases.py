"""Thermokon EnOcean sensor/device aliases.

Maps Thermokon device types to their standard EnOcean EEP(s) so they can be
searched in the web UI as 'Thermokon <type>'. Sources: Thermokon Alterra
Base / EasySens datasheets (the mimes/get/<id> links) and the standard EEP
catalog. The aliases are search-only (like Eltako) - they are not shown as
separate dropdown entries.

For all SR65 types, 'SR65+' is added as an additional alias (the successor
soon to be released).
"""
from __future__ import annotations

#: Thermokon type -> list of standard EEPs it maps to (from datasheets)
THERMOKON_ALIASES = {
    # --- radiator valve actuators (A5-20-01) ---
    'SAB': ['A5-20-01'],
    'SAB+': ['A5-20-01'],
    'SAB05': ['A5-20-01'],
    'SAB05+': ['A5-20-01'],
    # --- room sensors / operating units (A5-10-xx) ---
    'SR04': ['A5-02-05', 'A5-10-01', 'A5-10-03', 'A5-10-04', 'A5-10-05',
             'A5-10-06', 'A5-10-07', 'A5-10-10', 'A5-10-11', 'A5-10-12',
             'A5-10-13', 'A5-10-14', 'A5-04-01'],
    'SR04 rH': ['A5-02-05', 'A5-10-05', 'A5-10-06', 'A5-04-01'],
    'SR04 CO2': ['A5-02-05', 'A5-10-05', 'A5-04-01'],
    'SR06': ['A5-10-01', 'A5-10-03', 'A5-10-05', 'A5-10-06', 'A5-02-05'],
    'SR07': ['A5-10-03', 'A5-10-05', 'A5-10-06', 'A5-10-10', 'A5-10-11',
             'A5-10-12', 'A5-10-13', 'A5-10-14', 'A5-02-05', 'A5-04-01'],
    'NOVOS 3 SR': ['A5-02-05', 'A5-04-01'],
    'NOVOS 3 SR rH': ['A5-02-05', 'A5-10-03', 'A5-10-05', 'A5-10-06',
                      'A5-10-10', 'A5-10-12', 'A5-10-13', 'A5-04-01'],
    # --- switches (F6-xx) ---
    'SRG02': ['F6-02-01'],
    'SRW03': ['F6-02-01'],
    'SRW03 BAT': ['F6-02-01'],
    'SRW03 Dual BAT': ['F6-02-01'],
    'EasySens Radio switch mini': ['F6-02-01'],
    'EasySens Radio Remote': ['F6-02-01'],
    # --- motion / occupancy (A5-07-01 / A5-08-xx) ---
    'SR-MOW': ['A5-07-01'],
    'SR-MOC': ['A5-07-01'],
    'SR-MDS': ['A5-07-01'],
    'MCS-SR OCC': ['A5-07-01'],
    'MCS-SR Temp_rH': ['A5-02-05', 'A5-04-01'],
    # --- SR65 family (all map to A5-20-01; add SR65+ successor alias) ---
    'SR65': ['A5-20-01'],
    'SR65 Li': ['A5-20-01'],
    'SR65 rH': ['A5-20-01'],
    'SR65 3AI': ['A5-20-01'],
    'SR65 DI': ['A5-20-01'],
    'SR-MI-HS': ['A5-20-01'],
    # --- outputs / transceivers (bidirectional D2 / A5-38-xx) ---
    'STC-DO 8 Type 1': ['A5-38-08'],
    'STC-DO 8 Type 2': ['A5-38-08'],
    'STC-DO 8 Type 3': ['A5-38-08'],
    'STC-DO 24V': ['A5-38-08'],
    'STC-DO': ['A5-38-08'],
    'SRC-AO MULTI': ['D2-01-08'],
    'SRC-AO DIM': ['D2-01-08'],
    'SRC-AO CLIMATE': ['D2-01-08'],
}


def thermokon_alias_map():
    """return {eep: [alias, ...]} with 'SR65+' added for every SR65 type."""
    out = {}
    for alias, eeps in THERMOKON_ALIASES.items():
        for eep in eeps:
            out.setdefault(eep, set()).add('Thermokon ' + alias)
        # successor alias for all SR65 types
        if alias.startswith('SR65'):
            for eep in eeps:
                out.setdefault(eep, set()).add('Thermokon SR65+')
    return {k: sorted(v) for k, v in out.items()}

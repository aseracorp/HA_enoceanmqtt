"""Thermokon EnOcean sensor/device aliases.

Maps Thermokon device types (from the Alterra Base / EasySens datasheets) to
the EnOcean EEPs their radio profile actually uses. Each alias is
'Thermokon <type>' so the device can be found in the web UI EEP field by
typing e.g. 'Thermokon SR65'.

All EEPs below were taken directly from the respective Thermokon datasheet
("OVERVIEW SUPPORTED EEPS" / "COMPATIBILITY LIST" / telegram tables), not
guessed. Aliases are search-only (like Eltako) - the dropdown itself is not
cluttered with duplicates.

For all SR65 types, 'SR65+' is also added as an alias (the successor that
will be released soon - it uses the same EEP family).
"""
from __future__ import annotations

#: Thermokon type -> standard EEPs from its datasheet
THERMOKON_ALIASES = {
    # --- radiator valve actuators (A5-20-01) ---
    'SAB': ['A5-20-01'],
    'SAB+': ['A5-20-01'],
    'SAB05': ['A5-20-01'],

    # --- room sensors / operating units ---
    'SR04': ['A5-02-05', 'A5-04-01', 'A5-10-01', 'A5-10-03', 'A5-10-04',
             'A5-10-05', 'A5-10-06', 'A5-10-07', 'A5-10-0C', 'A5-10-10',
             'A5-10-11', 'A5-10-12'],
    'SR04 rH': ['A5-02-05', 'A5-04-01', 'A5-10-03', 'A5-10-05', 'A5-10-06',
                'A5-10-07', 'A5-10-0C', 'A5-10-10', 'A5-10-11', 'A5-10-12'],
    'SR04 CO2': ['A5-09-04'],
    'SR07': ['A5-02-05', 'A5-04-01', 'A5-10-03', 'A5-10-05', 'A5-10-06',
             'A5-10-0C', 'A5-10-0D', 'A5-10-10', 'A5-10-11', 'A5-10-12',
             'A5-10-13', 'A5-10-14'],
    'NOVOS 3 SR': ['A5-02-05', 'A5-04-01'],
    'NOVOS 3 SR rH': ['A5-10-03', 'A5-10-05', 'A5-10-0C', 'A5-10-10',
                      'A5-10-12', 'A5-10-13'],
    'SR06 LCD': ['A5-10-02', 'A5-10-03', 'A5-10-04', 'A5-10-06', 'A5-10-11',
                 'A5-10-12', 'A5-10-22', 'A5-10-23', 'D2-11-01', 'D2-11-02',
                 'D2-11-03', 'D2-11-04', 'D2-11-05', 'D2-11-06', 'D2-11-07',
                 'D2-11-08', 'F6-02-01'],

    # --- switches / window contacts ---
    'SRG02': ['F6-10-00'],                       # window handle
    'SRW03': ['D5-00-01'],                       # window contact
    'SRW03 BAT': ['D5-00-01'],                    # window contact battery
    'SRW03 Dual BAT': ['D5-00-01'],               # window contact double battery
    'EasySens Radio switch mini': ['F6-02-01'],
    'EasySens Radio Remote': ['F6-02-01'],

    # --- motion / occupancy / light ---
    'SR-MOW': ['A5-07-01'],                      # wall motion (solar)
    'SR-MOC': ['A5-07-01'],                      # ceiling motion 360° (solar)
    'SR-MDS': ['A5-06-02', 'A5-07-01', 'A5-07-02', 'A5-07-03', 'A5-08-01',
               'A5-08-02', 'F6-02-01'],          # ceiling light+motion (solar)
    'MCS-SR OCC': ['A5-07-01'],
    'MCS-SR Temp_rH': ['A5-04-01'],

    # --- SR65 family ---
    'SR65': ['A5-02-01', 'A5-02-02', 'A5-02-03', 'A5-02-04', 'A5-02-05',
             'A5-02-06', 'A5-02-07', 'A5-02-08', 'A5-02-09', 'A5-02-0A',
             'A5-02-0B', 'A5-02-10', 'A5-02-11', 'A5-02-12', 'A5-02-13',
             'A5-02-14', 'A5-02-15', 'A5-02-16', 'A5-02-17', 'A5-02-18',
             'A5-02-19', 'A5-02-1A', 'A5-02-1B', 'A5-02-20', 'A5-02-30'],
    'SR65 Li': ['A5-06-01', 'A5-06-02', 'A5-06-03'],
    'SR65 rH': ['A5-04-01', 'A5-04-02', 'A5-04-03'],
    'SR65 3AI': ['A5-3F-7F'],
    'SR65 DI': ['A5-07-01', 'A5-07-02', 'A5-30-01', 'A5-30-02', 'D5-00-01',
                'F6-02-01', 'F6-03-01', 'F6-04-01'],
    'SR-MI-HS': ['A5-12-00'],

    # --- receivers / outputs (bidirectional) ---
    'SRC-AO MULTI': ['A5-02-05', 'A5-04-01', 'A5-10-01', 'A5-10-03',
                     'A5-10-04', 'A5-10-05', 'A5-10-06', 'A5-10-0C',
                     'A5-10-10', 'A5-10-11', 'A5-10-12', 'D5-00-01',
                     'F6-10-00'],
    'SRC-AO DIM': ['A5-02-05', 'A5-04-01', 'A5-10-01', 'A5-10-03',
                   'A5-10-04', 'A5-10-05', 'A5-10-06', 'A5-10-0C',
                   'A5-10-10', 'A5-10-11', 'A5-10-12', 'D5-00-01',
                   'F6-10-00'],
    'SRC-AO CLIMATE': ['A5-02-05', 'A5-04-01', 'A5-10-01', 'A5-10-03',
                       'A5-10-04', 'A5-10-05', 'A5-10-06', 'A5-10-0C',
                       'A5-10-10', 'A5-10-11', 'A5-10-12', 'D5-00-01',
                       'F6-10-00'],

    # --- transceivers / relays (bidirectional) ---
    'STC-DO': ['A5-02-01', 'A5-04-01', 'A5-06-01', 'A5-07-01', 'A5-08-01',
               'A5-09-02', 'A5-09-08', 'A5-10-01', 'A5-10-0A', 'A5-10-10',
               'A5-10-18', 'A5-10-1A', 'A5-10-22', 'A5-11-02', 'A5-14-01',
               'A5-14-02', 'A5-20-12', 'A5-30-01', 'A5-30-02', 'D5-00-01',
               'F6-02-01', 'F6-03-01', 'F6-04-01', 'F6-10-00'],
    'STC-DO 24V': ['A5-02-01', 'A5-04-01', 'A5-06-01', 'A5-07-01',
                   'A5-08-01', 'A5-09-02', 'A5-09-08', 'A5-10-01',
                   'A5-10-0A', 'A5-10-10', 'A5-10-18', 'A5-10-1A',
                   'A5-10-22', 'A5-11-02', 'A5-14-01', 'A5-14-02',
                   'A5-20-12', 'A5-30-01', 'A5-30-02', 'D5-00-01',
                   'F6-02-01', 'F6-03-01', 'F6-04-01', 'F6-10-00'],
    'STC-DO 8 Type 1': ['A5-02-05', 'A5-04-01', 'A5-07-01', 'A5-08-01',
                        'A5-09-04', 'A5-10-01', 'A5-10-02', 'A5-10-03',
                        'A5-10-04', 'A5-10-05', 'A5-10-06', 'A5-10-0C',
                        'A5-10-10', 'A5-10-11', 'A5-10-12', 'A5-10-13',
                        'A5-11-02', 'A5-20-01', 'A5-20-12', 'A5-30-01',
                        'D5-00-01', 'F6-02-01', 'F6-04-01', 'F6-10-00'],
    'STC-DO 8 Type 2': ['A5-02-05', 'A5-04-01', 'A5-07-01', 'A5-08-01',
                        'A5-09-04', 'A5-10-01', 'A5-10-02', 'A5-10-03',
                        'A5-10-04', 'A5-10-05', 'A5-10-06', 'A5-10-0C',
                        'A5-10-10', 'A5-10-11', 'A5-10-12', 'A5-10-13',
                        'A5-11-02', 'A5-20-01', 'A5-20-12', 'A5-30-01',
                        'D5-00-01', 'F6-02-01', 'F6-04-01', 'F6-10-00'],
    'STC-DO 8 Type 3': ['A5-07-01', 'A5-08-01', 'A5-11-01', 'A5-30-01',
                        'D5-00-01', 'F6-02-01', 'F6-10-00'],
}


def thermokon_alias_map():
    """return {eep: [alias, ...]} with 'SR65+' added for every SR65 type."""
    out = {}
    for alias, eeps in THERMOKON_ALIASES.items():
        for eep in eeps:
            out.setdefault(eep, set()).add('Thermokon ' + alias)
        if alias.startswith('SR65'):
            for eep in eeps:
                out.setdefault(eep, set()).add('Thermokon SR65+')
    return {k: sorted(v) for k, v in out.items()}

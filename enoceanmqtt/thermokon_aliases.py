"""Thermokon EnOcean sensor/device aliases.

Maps Thermokon device types to the EnOcean EEPs their radio profile actually
uses. The list was hand-verified against the Alterra Base / EasySens
datasheets; the SR06 / SR07 / NOVOS 3 SR variants follow the exact ordering
of the datasheet EEP tables.

Every alias is prefixed with 'Thermokon EasySens ' so a device can be found
in the web UI EEP field by typing e.g. 'Thermokon EasySens SR65'. Aliases
are search-only (like Eltako) - the dropdown itself is not cluttered with
duplicates.

For all SR65 types, 'SR65+' is also added as an alias (the successor that
will be released soon).
"""
from __future__ import annotations

PREFIX = 'Thermokon EasySens '

#: Thermokon type -> standard EEPs (hand-verified from the datasheets)
THERMOKON_ALIASES = {
    # --- radiator valve actuators (A5-20-01) ---
    'SAB': ['A5-20-01'],
    'SAB+': ['A5-20-01'],
    'SAB05': ['A5-20-01'],

    # --- SR06 LCD variants (each maps to its A5-xx + D2-11-xx SmartACK) ---
    'SR06 LCD 2T Temp': ['A5-10-03', 'D2-11-01'],
    'SR06 LCD 2T Temp_rH': ['A5-10-12', 'D2-11-02'],
    'SR06 LCD 4T Temp Typ 1': ['A5-10-04', 'D2-11-03'],
    'SR06 LCD 4T Temp_rH Typ 1': ['A5-10-22', 'D2-11-04'],
    'SR06 LCD 4T Temp Typ 2': ['A5-10-02', 'D2-11-05'],
    'SR06 LCD 4T Temp_rH Typ 2': ['A5-10-23', 'D2-11-06'],
    'SR06 LCD 4T Temp Typ 3': ['A5-10-06', 'D2-11-07'],
    'SR06 LCD 4T Temp_rH Typ 3': ['A5-10-11', 'D2-11-08'],
    'SR06 LCD 2T+Light Temp': ['A5-10-03', 'F6-02-01', 'D2-11-01'],
    'SR06 LCD 2T+Blind Temp': ['A5-10-03', 'F6-02-01', 'D2-11-01'],
    'SR06 LCD 2T+Blind Temp_rH': ['A5-10-12', 'F6-02-01', 'D2-11-02'],

    # --- SR07 variants ---
    'SR07 Temp': ['A5-02-05'],
    'SR07 Temp_rH': ['A5-04-01'],
    'SR07 P Temp': ['A5-10-03'],
    'SR07 T Temp': ['A5-10-0C'],
    'SR07 PT Temp': ['A5-10-05'],
    'SR07 MS Temp': ['A5-10-0D'],
    'SR07 PMS Temp': ['A5-10-06'],
    'SR07 P Temp_rH': ['A5-10-12'],
    'SR07 T Temp_rH': ['A5-10-13'],
    'SR07 PT Temp_rH': ['A5-10-10'],
    'SR07 MS Temp_rH': ['A5-10-14'],
    'SR07 PMS Temp_rH': ['A5-10-11'],

    # --- NOVOS 3 SR ---
    'NOVOS 3 SR Temp': ['A5-02-05', 'A5-04-01'],
    'NOVOS 3 SR Temp_rH': ['A5-10-03', 'A5-10-05', 'A5-10-0C', 'A5-10-10',
                           'A5-10-12', 'A5-10-13'],

    # --- switches / window contacts ---
    'SRG02': ['F6-10-00'],                       # window handle
    'SRW03': ['D5-00-01'],                       # window contact
    'SRW03 BAT': ['D5-00-01'],                    # window contact battery
    'SRW03 Dual BAT': ['D5-00-01'],               # window contact double battery
    'Radio switch': ['F6-02-01'],                 # = Funkschalter
    'Funkschalter': ['F6-02-01'],                 # German duplicate
    'Radio remote': ['F6-02-01'],                 # = Handsender
    'Handsender': ['F6-02-01'],                   # German duplicate

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
    # STC-DO8 Type 1 / Type 2: the transceiver sends its profile as the
    # 'superior control unit' EEP A5-20-12 (what it accepts is a receive
    # compatibility list, not its own device profile).
    'STC-DO 8 Type 1': ['A5-20-12'],
    'STC-DO 8 Type 2': ['A5-20-12'],
    'STC-DO 8 Type 3': ['A5-07-01', 'A5-08-01', 'A5-11-01', 'A5-30-01',
                        'D5-00-01', 'F6-02-01', 'F6-10-00'],
}


def thermokon_alias_map():
    """return {eep: [alias, ...]} with the 'Thermokon EasySens' prefix and
    the 'SR65+' successor added for every SR65 type."""
    out = {}
    for alias, eeps in THERMOKON_ALIASES.items():
        full = PREFIX + alias
        for eep in eeps:
            out.setdefault(eep, set()).add(full)
        if alias.startswith('SR65'):
            for eep in eeps:
                out.setdefault(eep, set()).add(PREFIX + 'SR65+')
    return {k: sorted(v) for k, v in out.items()}

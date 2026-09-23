# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""EEP profile fragment: F6 (RPS) rockers and handles + D5 (1BS) contact.

Merged into the full PROFILES by the package __init__. Built with the _build helpers;
checked by tests/protocol/test_profiles.py.
"""

# ruff: noqa
from enoceanmqtt.eep_engine._build import case, cond, enum, field, profile

PROFILES = {
    (0xd5, 0x00, 0x01): profile(0xd5, 0x00, 0x01, 'Single Input Contact',
        case((
            field('CO', 'Contact', 7, 1, 'enum', items=(enum('open', 0), enum('closed', 1))),
            field('LRN', 'Learn Button', 4, 1, 'enum', items=(enum('pressed', 0), enum('not pressed', 1))),
        )),
    ),
    (0xf6, 0x01, 0x01): profile(0xf6, 0x01, 0x01, 'Push Button',
        case((
            field('PB', 'Push button', 3, 1, 'enum', items=(enum('Released', 0), enum('Pressed & Hold', 1))),
        )),
    ),
    (0xf6, 0x02, 0x01): profile(0xf6, 0x02, 0x01, 'Light and Blind Control - Application Style 1',
        case((
            field('R1', 'Rocker 1st action', 0, 3, 'enum', items=(enum('Button AI', 0), enum('Button AO', 1), enum('Button BI', 2), enum('Button BO', 3))),
            field('EB', 'Energy bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
            field('R2', 'Rocker 2nd action', 4, 3, 'enum', items=(enum('Button AI', 0), enum('Button AO', 1), enum('Button BI', 2), enum('Button BO', 3))),
            field('SA', '2nd action', 7, 1, 'enum', items=(enum('No 2nd action', 0), enum('2nd action valid', 1))),
        ), status_fields=(field('T21', 'T21', 2, 1, 'bool'), field('NU', 'NU', 3, 1, 'bool'))),
    ),
    (0xf6, 0x02, 0x02): profile(0xf6, 0x02, 0x02, 'Light and Blind Control - Application Style 2',
        case((
            field('R1', 'Rocker 1st action', 0, 3, 'enum', items=(enum('Button AI:', 0), enum('Button A0:', 1), enum('Button BI:', 2), enum('Button B0:', 3))),
            field('EB', 'Energy Bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
            field('R2', 'Rocker 2nd action', 4, 3, 'enum', items=(enum('Button AI:', 0), enum('Button A0:', 1), enum('Button BI:', 2), enum('Button B0:', 3))),
            field('SA', '2nd Action', 7, 1, 'enum', items=(enum('No 2nd action', 0), enum('2nd action valid', 1))),
        ), conditions=(cond('status', 2, 1, 1), cond('status', 3, 1, 1)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=1), field('', 'NU', 3, 1, 'fixed', fixed_value=1))),
        case((
            field('R1', 'Number of buttons pressed simultaneously (other bit combinations are not valid)', 0, 3, 'enum', items=(enum('no button', 0), enum('3 or 4 buttons', 3))),
            field('EB', 'Energy Bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
        ), conditions=(cond('status', 2, 1, 1), cond('status', 3, 1, 0)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=1), field('', 'NU', 3, 1, 'fixed', fixed_value=0))),
    ),
    (0xf6, 0x02, 0x03): profile(0xf6, 0x02, 0x03, 'Light Control - Application Style 1',
        case((
            field('RA', 'Rocker action', 0, 8, 'enum', items=(enum('Button A0:', 48), enum('Button A1:', 16), enum('Button B0:', 112), enum('Button B1:', 80))),
        ), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=1), field('', 'NU', 3, 1, 'fixed', fixed_value=1))),
    ),
    (0xf6, 0x02, 0x04): profile(0xf6, 0x02, 0x04, 'Light and blind control ERP2',
        case((
            field('EBO', 'Energy Bow', 0, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
            field('BC', 'Button coding', 1, 1, 'enum', items=(enum('button', 0),)),
            field('RBI', 'BI', 4, 1, 'enum', items=(enum('not pressed', 0), enum('pressed', 1))),
            field('RB0', 'B0', 5, 1, 'enum', items=(enum('not pressed', 0), enum('pressed', 1))),
            field('RAI', 'AI', 6, 1, 'enum', items=(enum('not pressed', 0), enum('pressed', 1))),
            field('RA0', 'A0', 7, 1, 'enum', items=(enum('not pressed', 0), enum('pressed', 1))),
        )),
    ),
    (0xf6, 0x03, 0x01): profile(0xf6, 0x03, 0x01, 'Light and Blind Control - Application Style 1',
        case((
            field('R1', 'Rocker 1st action', 0, 3, 'enum', items=(enum('Button AI:', 0), enum('Button A0:', 1), enum('Button BI:', 2), enum('Button B0:', 3), enum('Button CI:', 4), enum('Button C0:', 5), enum('Button DI:', 6), enum('Button D0:', 7))),
            field('EB', 'Energy Bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
            field('R2', 'Rocker 2nd action', 4, 3, 'enum', items=(enum('Button AI:', 0), enum('Button A0:', 1), enum('Button BI:', 2), enum('Button B0:', 3), enum('Button CI:', 4), enum('Button C0:', 5), enum('Button DI:', 6), enum('Button D0:', 7))),
            field('SA', '2nd Action', 7, 1, 'enum', items=(enum('No 2nd action', 0), enum('2nd action valid', 1))),
        ), conditions=(cond('status', 2, 1, 0), cond('status', 3, 1, 1)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=0), field('', 'NU', 3, 1, 'fixed', fixed_value=1))),
        case((
            field('R1', 'Number of buttons pressed simultaneously', 0, 3, 'enum', items=(enum('no Button pressed', 0), enum('2 buttons pressed', 1), enum('3 buttons pressed', 2), enum('4 buttons pressed', 3), enum('5 buttons pressed', 4), enum('6 buttons pressed', 5), enum('7 buttons pressed', 6), enum('8 buttons pressed', 7))),
            field('EB', 'Energy Bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
        ), conditions=(cond('status', 2, 1, 0), cond('status', 3, 1, 0)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=0), field('', 'NU', 3, 1, 'fixed', fixed_value=0))),
    ),
    (0xf6, 0x03, 0x02): profile(0xf6, 0x03, 0x02, 'Light and Blind Control - Application Style 2',
        case((
            field('R1', 'Rocker 1st action', 0, 3, 'enum', items=(enum('Button AI:', 0), enum('Button A0:', 1), enum('Button BI:', 2), enum('Button B0:', 3), enum('Button CI:', 4), enum('Button C0:', 5), enum('Button DI:', 6), enum('Button D0:', 7))),
            field('EB', 'Energy Bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
            field('R2', 'Rocker 2nd action', 4, 3, 'enum', items=(enum('Button AI:', 0), enum('Button A0:', 1), enum('Button BI:', 2), enum('Button B0:', 3), enum('Button CI:', 4), enum('Button C0:', 5), enum('Button DI:', 6), enum('Button D0:', 7))),
            field('SA', '2nd Action', 7, 1, 'enum', items=(enum('No 2nd action', 0), enum('2nd action valid', 1))),
        ), conditions=(cond('status', 2, 1, 0), cond('status', 3, 1, 1)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=0), field('', 'NU', 3, 1, 'fixed', fixed_value=1))),
        case((
            field('R1', 'Number of buttons pressed simultaneously', 0, 3, 'enum', items=(enum('no button pressed', 0), enum('2 buttons pressed', 1), enum('3 buttons pressed', 2), enum('4 buttons pressed', 3), enum('5 buttons pressed', 4), enum('6 buttons pressed', 5), enum('7 buttons pressed', 6), enum('8 buttons pressed', 7))),
            field('EB', 'Energy Bow', 3, 1, 'enum', items=(enum('released', 0), enum('pressed', 1))),
        ), conditions=(cond('status', 2, 1, 0), cond('status', 3, 1, 0)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=0), field('', 'NU', 3, 1, 'fixed', fixed_value=0))),
    ),
    (0xf6, 0x04, 0x01): profile(0xf6, 0x04, 0x01, 'Key Card Activated Switch',
        case((
            field('KC', 'Key Card', 0, 8, 'enum', items=(enum('inserted\n  (0x70)', 112),)),
        ), conditions=(cond('status', 2, 1, 1), cond('status', 3, 1, 1)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=1), field('', 'NU', 3, 1, 'fixed', fixed_value=1))),
        case((
            field('KC', 'Key Card', 0, 8, 'enum', items=(enum('taken out', 0),)),
        ), conditions=(cond('status', 2, 1, 1), cond('status', 3, 1, 0)), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=1), field('', 'NU', 3, 1, 'fixed', fixed_value=0))),
    ),
    (0xf6, 0x04, 0x02): profile(0xf6, 0x04, 0x02, 'Key Card Activated Switch ERP2',
        case((
            field('EBO', 'Energy Bow', 0, 1, 'enum', items=(enum('taken out', 0), enum('card inserted', 1))),
            field('BC', 'Button coding', 1, 1, 'enum', items=(enum('button', 0),)),
            field('SOC', 'State of card', 5, 1, 'enum', items=(enum('taken out', 0), enum('card inserted', 1))),
        )),
    ),
    (0xf6, 0x05, 0x00): profile(0xf6, 0x05, 0x00, 'Wind Speed Threshold Detector',
        case((
            field('WND', 'Status', 0, 8, 'enum', items=(enum('Wind speed below threshold (Alarm OFF)', 0), enum('Wind speed exceeds threshold (Alarm ON)', 16), enum('Energy LOW', 48))),
        )),
    ),
    (0xf6, 0x05, 0x01): profile(0xf6, 0x05, 0x01, 'Liquid Leakage Sensor (mechanic harvester)',
        case((
            field('WAS', 'Water sensor', 0, 8, 'enum', items=(enum('Water detected', 17),)),
        ), status_fields=(field('', 'T21', 2, 1, 'fixed', fixed_value=1), field('', 'NU', 3, 1, 'fixed', fixed_value=1))),
    ),
    (0xf6, 0x05, 0x02): profile(0xf6, 0x05, 0x02, 'Smoke Detector',
        case((
            field('SMO', 'Status', 0, 8, 'enum', items=(enum('Smoke Alarm OFF', 0), enum('Smoke Alarm ON', 16), enum('Energy LOW', 48))),
        )),
    ),
    (0xf6, 0x10, 0x00): profile(0xf6, 0x10, 0x00, 'Window Handle',
        case((
            field('WIN', 'Window handle', 2, 2, 'enum', items=(
                enum('Moved from up to vertical', 0),
                enum('Moved from vertical to up', 1),
                enum('Moved from down to vertical', 2),
                enum('Moved from vertical to down', 3),
            )),
            field('T21', 'T21', 2, 1, 'bool'),
            field('NU', 'NU', 3, 1, 'bool'),
        )),
    ),
    (0xf6, 0x10, 0x01): profile(0xf6, 0x10, 0x01, 'Window Handle ERP2',
        case((
            field('HC', 'Handle coding', 1, 1, 'enum', items=(enum('handle', 1),)),
            field('HVL', 'Handle value', 4, 4, 'enum', items=(enum('Moved from up to right.'), enum('Moved from right to down.', 15), enum('Moved from down to left.'), enum('Moved from left to up.', 13), enum('Moved from up to left.'), enum('Moved from left  to down.', 15), enum('Moved from down to right.'), enum('Moved from right to up.', 13))),
        )),
    ),
}

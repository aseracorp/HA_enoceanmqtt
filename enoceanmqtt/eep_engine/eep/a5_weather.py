# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""EEP profile fragment: A5-13 environmental / weather.

Merged into the full PROFILES by the package __init__. Built with the _build helpers;
checked by tests/protocol/test_profiles.py.
"""

# ruff: noqa
from enoceanmqtt.eep_engine._build import LRNB_4BS, case, cond, enum, field, profile

PROFILES = {
    (0xa5, 0x13, 0x01): profile(0xa5, 0x13, 0x01, 'Weather Station',
        case((
            field('DWS', 'Dawn sensor', 0, 8, 'value', unit='lx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=999.0),
            field('TMP', 'Outdoor Temp', 8, 8, 'value', unit='°C', range_min=0.0, range_max=255.0, scale_min=-40.0, scale_max=80.0),
            field('WND', 'Wind speed', 16, 8, 'value', unit='m/s', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=70.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('Identifier {value}', minimum=0, maximum=15),)),
            field('D/N', 'Day / Night', 29, 1, 'enum', items=(enum('Day', 0), enum('Night', 1))),
            field('RAN', 'Rain Indication', 30, 1, 'enum', items=(enum('No Rain', 0), enum('Rain', 1))),
        ), conditions=(cond('data', 24, 4, 1),)),
        case((
            field('SNW', 'Sun - West', 0, 8, 'value', unit='klx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=150.0),
            field('SNS', 'Sun - South', 8, 8, 'value', unit='klx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=150.0),
            field('SNE', 'Sun - East', 16, 8, 'value', unit='klx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=150.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('Identifier {value}', minimum=0, maximum=15),)),
            field('HEM', 'Hemisphere', 29, 1, 'enum', items=(enum('North', 0), enum('South', 1))),
        ), conditions=(cond('data', 24, 4, 2),)),
        case((
            field('DY', 'Day', 3, 5, 'value', range_min=1.0, range_max=31.0, scale_min=1.0, scale_max=31.0),
            field('MTH', 'Month', 12, 4, 'value', range_min=1.0, range_max=12.0, scale_min=1.0, scale_max=12.0),
            field('YR', 'Year', 17, 7, 'value', range_min=0.0, range_max=99.0, scale_min=2000.0, scale_max=2099.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('Identifier {value}', minimum=0, maximum=15),)),
            field('SRC', 'Source', 31, 1, 'enum', items=(enum('Real Time Clock', 0), enum('GPS or equivalent (e.g. DCF77, WWV)', 1))),
        ), conditions=(cond('data', 24, 4, 3),)),
        case((
            field('WDY', 'Weekday', 0, 3, 'enum', items=(enum('Monday', 1), enum('Tuesday', 2), enum('Wednesday', 3), enum('Thursday', 4), enum('Friday', 5), enum('Saturday', 6), enum('Sunday', 7))),
            field('HR', 'Hour', 3, 5, 'value', range_min=0.0, range_max=23.0, scale_min=0.0, scale_max=23.0),
            field('MIN', 'Minute', 10, 6, 'value', range_min=0.0, range_max=59.0, scale_min=0.0, scale_max=59.0),
            field('SEC', 'Second', 18, 6, 'value', range_min=0.0, range_max=59.0, scale_min=0.0, scale_max=59.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('Identifier {value}', minimum=0, maximum=15),)),
            field('TMF', 'Time Format', 29, 1, 'enum', items=(enum('24 Hours', 0), enum('12 Hours', 1))),
            field('A/PM', 'AM/PM', 30, 1, 'enum', items=(enum('AM', 0), enum('PM', 1))),
            field('SRC', 'Source', 31, 1, 'enum', items=(enum('Real Time Clock', 0), enum('GPS or equivalent (e.g. DCF77, WWV)', 1))),
        ), conditions=(cond('data', 24, 4, 4),)),
        case((
            field('ELV', 'Elevation', 0, 8, 'value', unit='°', range_min=0.0, range_max=180.0, scale_min=-90.0, scale_max=90.0),
            field('AZM', 'Azimut', 15, 9, 'value', unit='°', range_min=0.0, range_max=359.0, scale_min=0.0, scale_max=359.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('Identifier {value}', minimum=0, maximum=15),)),
        ), conditions=(cond('data', 24, 4, 5),)),
        case((
            field('LAT(MSB)', 'Latitude(MSB)', 0, 4, 'value', range_min=0.0, range_max=15.0, scale_min=0.0, scale_max=15.0),
            field('LOT(MSB)', 'Longitude(MSB)', 4, 4, 'value', range_min=0.0, range_max=15.0, scale_min=0.0, scale_max=15.0),
            field('LAT(LSB)', 'Latitude(LSB)', 8, 8, 'value', unit='°', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=255.0),
            field('LOT(LSB)', 'Longitude(LSB)', 16, 8, 'value', unit='°', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=255.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('Identifier {value}', minimum=0, maximum=15),)),
        ), conditions=(cond('data', 24, 4, 6),)),
    ),
    (0xa5, 0x13, 0x02): profile(0xa5, 0x13, 0x02, 'Sun Intensity',
        case((
            LRNB_4BS,
            field('SNW', 'Sun – West', 0, 8, 'value', unit='klx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=150.0),
            field('SNS', 'Sun – South', 8, 8, 'value', unit='klx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=150.0),
            field('SNE', 'Sun – East', 16, 8, 'value', unit='klx', range_min=0.0, range_max=255.0, scale_min=0.0, scale_max=150.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('', 2),)),
            field('HEM', 'Hemisphere', 29, 1, 'enum', items=(enum('North', 0), enum('South', 1))),
        )),
    ),
    (0xa5, 0x13, 0x03): profile(0xa5, 0x13, 0x03, 'Date Exchange',
        case((
            LRNB_4BS,
            field('DY', 'Day', 3, 5, 'value', unit='N/A', range_min=1.0, range_max=31.0, scale_min=1.0, scale_max=31.0),
            field('MTH', 'Month', 12, 4, 'value', unit='N/A', range_min=1.0, range_max=12.0, scale_min=1.0, scale_max=12.0),
            field('YR', 'Year', 17, 7, 'value', unit='N/A', range_min=0.0, range_max=99.0, scale_min=2000.0, scale_max=2099.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('', 3),)),
            field('SRC', 'Source', 31, 1, 'enum', items=(enum('Real Time Clock', 0), enum('GPS or equivalent (e.g. DCF77, WWV)', 1))),
        )),
    ),
    (0xa5, 0x13, 0x04): profile(0xa5, 0x13, 0x04, 'Time and Day Exchange',
        case((
            LRNB_4BS,
            field('WDY', 'Weekday', 0, 3, 'enum', items=(enum('Monday', 1), enum('Tuesday', 2), enum('Wednesday', 3), enum('Thursday', 4), enum('Friday', 5), enum('Saturday', 6), enum('Sunday', 7))),
            field('HR', 'Hour', 3, 5, 'value', unit='N/A', range_min=0.0, range_max=23.0, scale_min=0.0, scale_max=23.0),
            field('MIN', 'Minute', 10, 6, 'value', unit='N/A', range_min=0.0, range_max=59.0, scale_min=0.0, scale_max=59.0),
            field('SEC', 'Second', 18, 6, 'value', unit='N/A', range_min=0.0, range_max=59.0, scale_min=0.0, scale_max=59.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('', 4),)),
            field('TMF', 'Time Format', 29, 1, 'enum', items=(enum('24 hours', 0), enum('12 hours', 1))),
            field('A/PM', 'AM/PM', 30, 1, 'enum', items=(enum('AM', 0), enum('PM', 1))),
            field('SRC', 'Source', 31, 1, 'enum', items=(enum('Real Time Clock', 0), enum('GPS or equivalent (e.g. DCF77, WWV)', 1))),
        )),
    ),
    (0xa5, 0x13, 0x05): profile(0xa5, 0x13, 0x05, 'Direction Exchange',
        case((
            LRNB_4BS,
            field('ELV', 'Elevation', 0, 8, 'value', unit='°', range_min=0.0, range_max=180.0, scale_min=-90.0, scale_max=90.0),
            field('AZM', 'Azimut', 15, 9, 'value', unit='°', range_min=0.0, range_max=359.0, scale_min=0.0, scale_max=359.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('', 5),)),
        )),
    ),
    (0xa5, 0x13, 0x06): profile(0xa5, 0x13, 0x06, 'Geographic Position Exchange',
        case((
            LRNB_4BS,
            field('LAT(MSB)', 'Latitude(MSB)', 0, 4, 'value'),
            field('LOT(MSB)', 'Longitude(MSB)', 4, 4, 'value'),
            field('LAT(LSB)', 'Latitude(LSB)', 8, 8, 'value', unit='°', range_min=0.0, range_max=4095.0, scale_min=-90.0, scale_max=90.0),
            field('LOT(LSB)', 'Longitude(LSB)', 16, 8, 'value', unit='°', range_min=0.0, range_max=4095.0, scale_min=-180.0, scale_max=180.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('', 6),)),
        )),
    ),
    (0xa5, 0x13, 0x07): profile(0xa5, 0x13, 0x07, 'Wind Sensor',
        case((
            LRNB_4BS,
            field('WD', 'Wind Direction', 4, 4, 'enum', items=(enum('NNE', 0), enum('NE', 1), enum('ENE', 2), enum('E', 3), enum('ESE', 4), enum('SE', 5), enum('SSE', 6), enum('S', 7), enum('SSW', 8), enum('SW', 9), enum('WSW', 10), enum('W', 11), enum('WNW', 12), enum('NW', 13), enum('NNW', 14), enum('N', 15))),
            field('AWS', 'Average Wind Speed', 8, 8, 'value', unit='mph', range_min=0.0, range_max=255.0, scale_min=1.0, scale_max=199.9),
            field('MWS', 'Maximum Wind Speed', 16, 8, 'value', unit='mph', range_min=0.0, range_max=255.0, scale_min=1.0, scale_max=199.9),
            field('BS', 'Battery Status', 31, 1, 'enum', items=(enum('Battery okay', 0), enum('Battery low', 1))),
        )),
    ),
    (0xa5, 0x13, 0x08): profile(0xa5, 0x13, 0x08, 'Rain Sensor',
        case((
            LRNB_4BS,
            field('RAS', 'Rainfall Adjust Sign', 1, 1, 'enum', items=(enum('Negative', 0), enum('Positive', 1))),
            field('RFA', 'Rainfall Adjust', 2, 6, 'enum', items=(enum('', minimum=0, maximum=39, scale_min=0.0, scale_max=3.9, unit='%'), enum('Reserved', minimum=40, maximum=63))),
            field('RFC', 'Rainfall Count', 8, 16, 'value', range_min=0.0, range_max=65535.0, scale_min=0.0, scale_max=65535.0),
            field('BS', 'Battery Status', 31, 1, 'enum', items=(enum('Battery okay', 0), enum('Battery low', 1))),
        )),
    ),
    (0xa5, 0x13, 0x10): profile(0xa5, 0x13, 0x10, 'Sun position and radiation',
        case((
            LRNB_4BS,
            field('D/N', 'Day / Night', 7, 1, 'enum', items=(enum('Day', 0), enum('Night', 1))),
            field('SNE', 'Sun Elevation', 0, 7, 'value', unit='°', range_min=0.0, range_max=90.0, scale_min=0.0, scale_max=90.0),
            field('SNA', 'Sun Azimuth', 8, 8, 'value', unit='°', range_min=0.0, range_max=180.0, scale_min=-90.0, scale_max=90.0),
            field('SRA (MSB)', 'Solar Radiation (MSB)', 16, 8, 'value'),
            field('SRA (LSB)', 'Solar Radiation (LSB)', 29, 3, 'value', unit='W/m2', range_min=0.0, range_max=2000.0, scale_min=0.0, scale_max=2000.0),
            field('ID', 'Identifier', 24, 4, 'enum', items=(enum('', 7),)),
        )),
    ),
    (0xa5, 0x13, 0x11): profile(0xa5, 0x13, 0x11, 'Noise',
        case((
            field('', 'Average sound level', 0, 10, 'value', unit='0.1 dBA', range_min=0.0, range_max=1023.0, scale_min=17.6, scale_max=120.0),
            field('', 'Pic sound level', 10, 10, 'value', unit='0.1 dBA', range_min=0.0, range_max=1023.0, scale_min=17.6, scale_max=120.0),
            field('', 'Source localization', 20, 8, 'enum', items=(enum('', minimum=0, maximum=180, scale_min=-90.0, scale_max=90.0, unit='Degree'), enum('Reserved', minimum=181, maximum=255))),
            field('', 'LrnBit', 28, 1, 'enum', items=(enum('Teach-in telegram', 0), enum('Data Telegram', 1))),
            field('', 'Localization active', 29, 1, 'enum', items=(enum('No localization', 0), enum('Localization active', 1))),
        )),
    ),
}

# Author: Marc Alexandre K. <marcalexandrek-developer@yahoo.fr>
"""persistent store for sensors that are added through the web interface

Sensors added via the web UI (e.g. through the EnOcean UTE / Universal Teach-In
telegram) are stored separately from the static configuration file so that the
configuration file can stay read-only (e.g. when it is mounted from a read-only
volume).

Each stored sensor uses the *bare* section name (without the ``mqtt_prefix``),
matching how sections are named in the INI configuration file. The prefix is
prepended when the sensors are merged into the running configuration.
"""
import json
import logging
import os


class SensorStore:
    """thin JSON-backed store for dynamically added sensors"""

    def __init__(self, path):
        self.path = path
        self._sensors = []
        self._load()

    # ------------------------------------------------------------------ read
    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, 'r', encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._sensors = data
            else:
                self._sensors = data.get('sensors', [])
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Cannot read sensor store %s: %s", self.path, exc)
            self._sensors = []

    def all(self):
        """return a copy of all stored sensors"""
        return list(self._sensors)

    def get(self, name):
        """return a single stored sensor by bare name, or None"""
        for sensor in self._sensors:
            if sensor.get('name') == name:
                return dict(sensor)
        return None

    # ----------------------------------------------------------------- write
    def add(self, sensor):
        """add (or replace) a sensor, returns the stored sensor"""
        sensor = dict(sensor)
        # replace existing sensor with the same name
        self._sensors = [s for s in self._sensors if s.get('name') != sensor.get('name')]
        self._sensors.append(sensor)
        self._save()
        return sensor

    def remove(self, name):
        """remove a sensor by bare name, returns True if something was removed"""
        before = len(self._sensors)
        self._sensors = [s for s in self._sensors if s.get('name') != name]
        self._save()
        return len(self._sensors) < before

    def _save(self):
        if not self.path:
            return
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding="utf-8") as f:
                json.dump({'sensors': self._sensors}, f, indent=2)
            os.replace(tmp, self.path)
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Cannot write sensor store %s: %s", self.path, exc)

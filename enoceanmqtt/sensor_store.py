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

    def update(self, name, changes):
        """update fields of a stored sensor by bare name.

        ``changes`` is a dict of the fields to set (e.g. ``{'name': ...,
        'rorg': ..., 'func': ..., 'type': ...}``). If ``changes`` contains a
        new ``name``, the sensor is renamed. Returns the stored sensor or None.
        """
        for i, sensor in enumerate(self._sensors):
            if sensor.get('name') == name:
                updated = dict(sensor)
                updated.update(changes)
                # renaming: keep position stable, update the entry in place
                self._sensors[i] = updated
                self._save()
                return dict(updated)
        return None

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


class HistoryStore:
    """JSON-backed rolling history of decoded values per device address.

    The gateway keeps only an in-memory rolling buffer by default; persisting
    to a file lets the web UI value graph show data across restarts. Each
    device address maps to a list of {values, ts} entries (oldest first).
    """

    def __init__(self, path, max_entries=500):
        self.path = path
        self.max_entries = max_entries
        self._data = {}
        self._load()

    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, 'r', encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = data
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Cannot read history store %s: %s", self.path, exc)
            self._data = {}

    def _save(self):
        if not self.path:
            return
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except Exception as exc:   # pylint: disable=broad-except
            logging.error("Cannot write history store %s: %s", self.path, exc)

    def append(self, address, entry):
        hist = self._data.setdefault(str(address), [])
        hist.append(entry)
        if len(hist) > self.max_entries:
            del hist[:len(hist) - self.max_entries]
        self._save()

    def get(self, address, limit=None):
        hist = self._data.get(str(address), [])
        if limit:
            hist = hist[-limit:]
        return hist

    def latest(self, address):
        hist = self._data.get(str(address), [])
        return hist[-1] if hist else None

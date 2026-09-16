"""Persistent cover-position store (TinyDB), keyed by EnOcean address.

Cover positions for Eltako FSB-type shutter actuators only exist implicitly
(running-time telegrams accumulate onto the last known position). Without
persistence a gateway restart would forget where the blind is. We reuse the
same JSON/TinyDB approach as the rest of the project (web sensor store /
device DB) so nothing new is installed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time

from tinydb import TinyDB, Query

logger = logging.getLogger(__name__)


class CoverStore:
    """Store/retrieve the absolute cover position keyed by device address."""

    def __init__(self, db_file):
        if not db_file:
            db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'cover_positions.json')
        self._db = TinyDB(db_file, storage=self._corruption_tolerant_storage(db_file),
                          indent=4)

    @staticmethod
    def _corruption_tolerant_storage(db_file):
        from tinydb.storages import JSONStorage as _JSONStorage

        class _TolerantStorage(_JSONStorage):
            def read(self):
                try:
                    return super().read()
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    try:
                        backup = db_file + '.corrupt.' + time.strftime('%Y%m%d%H%M%S')
                        shutil.copyfile(db_file, backup)
                        logger.error("Cover store %s corrupted (%s); backed up to %s",
                                     db_file, exc, backup)
                    except Exception:   # pylint: disable=broad-except
                        pass
                    return None
        return _TolerantStorage

    def get_position(self, address):
        """Persisted cover position for an address (int) or None."""
        dev = Query()
        hit = self._db.get(dev.address == str(address))
        return hit.get('position') if hit else None

    def set_position(self, address, position):
        """Persist the cover position for an address."""
        dev = Query()
        self._db.upsert({'address': str(address), 'position': int(position)},
                        dev.address == str(address))

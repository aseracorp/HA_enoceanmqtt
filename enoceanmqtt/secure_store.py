"""Persistent rolling-code (RLC) store for secure EnOcean devices (TinyDB).

Rolling codes are monotonic per device and MUST survive restarts, otherwise
a reboot would make the gateway accept a replayed (old) telegram. Stored
keyed by EnOcean address, exactly like the cover positions store.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time

from tinydb import TinyDB, Query

logger = logging.getLogger(__name__)


class SecureStore:
    """Store/retrieve the last-accepted rolling code keyed by device address."""

    def __init__(self, db_file):
        if not db_file:
            db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'secure_rlc.json')
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
                        logger.error("Secure store %s corrupted (%s); backed up to %s",
                                     db_file, exc, backup)
                    except Exception:   # pylint: disable=broad-except
                        pass
                    return None
        return _TolerantStorage

    def get_rlc(self, address):
        """last-accepted rolling code for an address (int) or 0."""
        dev = Query()
        hit = self._db.get(dev.address == str(address))
        return int(hit.get('rlc', 0)) if hit else 0

    def set_rlc(self, address, rlc):
        """persist the next-expected rolling code for an address."""
        dev = Query()
        self._db.upsert({'address': str(address), 'rlc': int(rlc)},
                        dev.address == str(address))

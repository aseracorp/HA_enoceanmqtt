## 2026-03-05 - DoS via Dictionary Modification During Iteration
**Vulnerability:** A Denial of Service (DoS) vulnerability existed in `enoceanmqtt/communicator.py` where receiving a malformed JSON message over MQTT would cause a `RuntimeError` and crash the MQTT processing thread.
**Learning:** Python prohibits modifying (adding or deleting items) a dictionary while iterating over it directly. This common programming pattern can become a security risk when the iteration is driven by external, untrusted input.
**Prevention:** Always iterate over a copy of the dictionary keys using `list(dict_obj)` or similar when deletions might occur within the loop. This ensures the iterator remains valid even if the underlying dictionary is modified.

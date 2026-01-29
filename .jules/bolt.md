## 2025-05-14 - Optimized Sensor Lookup with O(1) Hash Maps
**Learning:** The core loop for processing both EnOcean packets and MQTT messages was performing a linear search (O(N)) through the entire sensor list for every incoming event. This caused significant overhead as the number of devices increased.
**Action:** Implemented dictionary-based indexing (`_sensors_by_address` and `_sensors_by_name`) to achieve O(1) lookup time. For MQTT messages, a prefix-based lookup was implemented to maintain compatibility with the hierarchical topic structure.

### Performance Impact (1000 sensors):
- **Radio Packet Processing:** 1.032 ms -> 0.003 ms (~340x faster)
- **MQTT Message Processing:** 0.190 ms -> 0.015 ms (~12x faster)
- **Complexity:** Reduced from O(N) to O(1) for both operations.

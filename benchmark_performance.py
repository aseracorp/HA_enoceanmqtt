import time
import random
import logging
import datetime
from enoceanmqtt.communicator import Communicator
from enocean.protocol.packet import RadioPacket
from enocean.protocol.constants import RORG
import enocean.utils

# Disable logging to avoid overhead during benchmarking
logging.getLogger().setLevel(logging.CRITICAL)

class MockMQTTMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload

class BenchCommunicator(Communicator):
    def __init__(self, config, sensors):
        self.conf = config
        self.sensors = sensors
        self._index_sensors()
        self.enocean_sender = [0xDE, 0xAD, 0xBE, 0xEF]

        class Mock:
            def start(self): pass
            def loop_start(self): pass
            def connect_async(self, *args, **kwargs): pass
            def publish(self, *args, **kwargs): pass
            def subscribe(self, *args, **kwargs): pass
            def username_pw_set(self, *args, **kwargs): pass
            def is_alive(self): return False
            def stop(self): pass
            def loop_stop(self): pass
            def disconnect(self): pass
            def loop_forever(self): pass

        self.mqtt = Mock()
        self.enocean = Mock()
        self.enocean.base_id = [0xDE, 0xAD, 0xBE, 0xEF]

    def _read_packet(self, packet, sensor):
        # Override to avoid expensive EEP parsing if we only want to measure search
        # But wait, we want to measure the whole thing to see real impact.
        # Let's keep it as is, but maybe mock packet.parse_eep if it's too slow.
        pass

    def _send_message(self, sensor, clear):
        # Avoid real sending
        pass

def generate_sensors(count):
    sensors = []
    for i in range(count):
        sensors.append({
            'name': f'enocean/sensor_{i}',
            'address': 0x01000000 + i,
            'rorg': RORG.BS4,
            'func': 0x02,
            'type': 0x05,
            'publish_json': '1',
            'publish_rssi': '1',
            'publish_date': '1'
        })
    return sensors

def benchmark():
    counts = [10, 50, 100, 500, 1000]
    iterations = 2000

    print(f"{'Sensors':<10} | {'Radio Packet (ms)':<20} | {'MQTT Message (ms)':<20}")
    print("-" * 55)

    for count in counts:
        sensors = generate_sensors(count)
        conf = {
            'mqtt_host': 'localhost',
            'enocean_port': '/dev/ttyUSB0',
            'mqtt_prefix': 'enocean/'
        }

        com = BenchCommunicator(conf, sensors)

        # Prepare test data
        test_packets = []
        # We'll use sensors that are late in the list to emphasize search time
        for i in range(count - 10, count):
            addr_int = 0x01000000 + i
            addr_bytes = [(addr_int >> (8 * j)) & 0xFF for j in reversed(range(4))]
            p = RadioPacket.create(RORG.BS4, 0x02, 0x05, sender=addr_bytes)
            p.dBm = -70
            p.received = datetime.datetime.now()
            test_packets.append(p)

        test_mqtt_messages = []
        for i in range(count - 10, count):
            topic = f"enocean/sensor_{i}/req/send"
            test_mqtt_messages.append(MockMQTTMessage(topic, b"send"))

        # Benchmark Radio Packet Processing
        start = time.time()
        for _ in range(iterations):
            p = random.choice(test_packets)
            com._process_radio_packet(p)
        radio_time = (time.time() - start) * 1000 / iterations

        # Benchmark MQTT Message Processing
        start = time.time()
        for _ in range(iterations):
            msg = random.choice(test_mqtt_messages)
            com._on_mqtt_message(None, None, msg)
        mqtt_time = (time.time() - start) * 1000 / iterations

        print(f"{count:<10} | {radio_time:<20.6f} | {mqtt_time:<20.6f}")

if __name__ == "__main__":
    benchmark()

import unittest
import unittest.mock
from enoceanmqtt.communicator import Communicator
from enocean.protocol.constants import RORG

class Mock:
    def start(self): pass
    def stop(self): pass
    def is_alive(self): return False
    def loop_start(self): pass
    def connect_async(self, *args, **kwargs): pass
    def username_pw_set(self, *args, **kwargs): pass

class TestLookup(unittest.TestCase):
    def setUp(self):
        self.sensors = [
            {'name': 'enocean/s1', 'address': 0x01000001, 'rorg': RORG.BS4},
            {'name': 'enocean/s2', 'address': 0x01000001, 'rorg': RORG.RPS},
            {'name': 'enocean/s3', 'address': 0x01000002, 'rorg': RORG.BS4},
            {'name': 'enocean/sub/s4', 'address': 0x01000003, 'rorg': RORG.BS4},
        ]
        self.conf = {
            'mqtt_host': 'localhost',
            'enocean_port': '/dev/ttyUSB0'
        }
        # Monkey patch Communicator to avoid side effects
        Communicator.mqtt = Mock()
        Communicator.enocean = Mock()
        # We need to avoid Communicator.__init__ trying to instantiate real classes
        # But since we already patched the class attributes and we are just testing lookup
        # we can just use a subclass that mocks the necessary parts.

        class TestCommunicator(Communicator):
            def __init__(self, config, sensors):
                self.conf = config
                self.sensors = sensors
                self._index_sensors()

        self.com = TestCommunicator(self.conf, self.sensors)

    def test_address_lookup(self):
        # Multiple sensors with same address
        potential = self.com._sensors_by_address.get(0x01000001)
        self.assertEqual(len(potential), 2)
        self.assertEqual(potential[0]['name'], 'enocean/s1')
        self.assertEqual(potential[1]['name'], 'enocean/s2')

        # Single sensor
        potential = self.com._sensors_by_address.get(0x01000002)
        self.assertEqual(len(potential), 1)
        self.assertEqual(potential[0]['name'], 'enocean/s3')

    def test_topic_lookup(self):
        # Exact match + /req/send
        matched = self.com._get_sensors_for_topic('enocean/s1/req/send')
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]['name'], 'enocean/s1')

        # Nested sensor
        matched = self.com._get_sensors_for_topic('enocean/sub/s4/req/send')
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]['name'], 'enocean/sub/s4')

        # No match
        matched = self.com._get_sensors_for_topic('unknown/topic')
        self.assertEqual(len(matched), 0)

        # Partial match but not followed by /
        matched = self.com._get_sensors_for_topic('enocean/s1req/send')
        self.assertEqual(len(matched), 0)

    def test_prefix_order(self):
        # Test if multiple prefix sensors are found in correct order
        self.sensors.append({'name': 'enocean/sub', 'address': 0x01000004, 'rorg': RORG.BS4})
        self.com._index_sensors()

        matched = self.com._get_sensors_for_topic('enocean/sub/s4/req/send')
        self.assertEqual(len(matched), 2)
        # Order should match self.sensors order: s4 is at index 3, sub is at index 4
        self.assertEqual(matched[0]['name'], 'enocean/sub/s4')
        self.assertEqual(matched[1]['name'], 'enocean/sub')

    def test_process_radio_packet_match(self):
        from enocean.protocol.packet import RadioPacket
        import datetime

        # Mock _read_packet to record calls
        self.com._read_packet = unittest.mock.Mock()

        # Packet from s1
        p = RadioPacket.create(RORG.BS4, 0x02, 0x05, sender=[0x01, 0x00, 0x00, 0x01])
        p.dBm = -70
        p.received = datetime.datetime.now()

        self.com._process_radio_packet(p)
        self.com._read_packet.assert_called_once()
        self.assertEqual(self.com._read_packet.call_args[0][1]['name'], 'enocean/s1')

    def test_process_radio_packet_no_match(self):
        from enocean.protocol.packet import RadioPacket
        self.com._read_packet = unittest.mock.Mock()

        # Packet from unknown sender
        p = RadioPacket.create(RORG.BS4, 0x02, 0x05, sender=[0x09, 0x09, 0x09, 0x09])
        self.com._process_radio_packet(p)
        self.com._read_packet.assert_not_called()

    def test_sensor_without_address_or_name(self):
        self.sensors.append({'rorg': RORG.BS4}) # No name, no address
        self.com._index_sensors()
        # Should not crash and should not be found by address or topic
        potential = self.com._sensors_by_address.get(None)
        self.assertIsNone(potential)

    def test_rorg_match_logic(self):
        # Case where multiple sensors have same address but different RORGs
        # setUp already has s1 (BS4) and s2 (RPS) at 0x01000001
        from enocean.protocol.packet import RadioPacket
        self.com._read_packet = unittest.mock.Mock()

        # RPS Packet
        p = RadioPacket.create(RORG.RPS, 0x00, 0x00, sender=[0x01, 0x00, 0x00, 0x01])
        p.dBm = -70
        import datetime
        p.received = datetime.datetime.now()

        self.com._process_radio_packet(p)
        self.com._read_packet.assert_called_once()
        self.assertEqual(self.com._read_packet.call_args[0][1]['name'], 'enocean/s2')

    def test_duplicate_names(self):
        # Add another sensor with name 'enocean/s1'
        self.sensors.append({'name': 'enocean/s1', 'address': 0x01000005, 'rorg': RORG.BS4})
        self.com._index_sensors()

        matched = self.com._get_sensors_for_topic('enocean/s1/req/send')
        self.assertEqual(len(matched), 2)
        self.assertEqual(matched[0]['address'], 0x01000001)
        self.assertEqual(matched[1]['address'], 0x01000005)

    def test_invalid_payload_return_value(self):
        # Mock MQTT message with invalid int payload
        class MockMsg:
            topic = 'enocean/s1/req/value'
            payload = b'not_an_int'

        # Should return False to match original behavior (None/False)
        result = self.com._mqtt_message_normal(MockMsg())
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()

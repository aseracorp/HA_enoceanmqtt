# -*- encoding: utf-8 -*-
import logging
import socket
import time

from enocean.communicators.communicator import Communicator
from enocean.protocol.constants import PACKET
from enocean.protocol.packet import Packet


class TCPClientCommunicator(Communicator):
    """socket communicator class for EnOcean radio"""
    logger = logging.getLogger('enocean.communicators.TCPClientCommunicator')

    def __init__(self, host='', port=9637):
        super().__init__()
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def run(self):
        self.logger.info('TCPClientCommunicator started')
        self.sock.settimeout(3)
        pinged = time.time()

        try:
            self.sock.connect((self.host, self.port))
        except Exception as e:
            self.logger.error('Exception occurred while connecting: %s', e)
            self.stop()

        self.sock.settimeout(0.5)

        while not self._stop_flag.is_set():
            # flush the transmit queue
            while True:
                packet = self._get_from_send_queue()
                if not packet:
                    break
                try:
                    self.sock.send(bytearray(packet.build()))
                    pinged = time.time()
                except Exception as e:
                    self.logger.error('Exception occurred while sending: %s', e)
                    self.stop()

            try:
                self._buffer.extend(bytearray(self.sock.recv(16)))
                self.parse()
            except socket.timeout:
                pass
            except ConnectionResetError as e:
                self.logger.error('Exception occurred while recv: %s', e)
                self.stop()
            except Exception as e:
                self.logger.error('Exception occurred while parsing: %s', e)

            # keep the connection alive with a common-command ping
            if time.time() > pinged + 30:
                self.send(Packet(PACKET.COMMON_COMMAND, data=[0x08]))
                pinged = time.time()

        self.sock.close()
        self.logger.info('TCPClientCommunicator stopped')
# -*- encoding: utf-8 -*-
import logging
import socket
import time

from enocean.communicators.communicator import Communicator
from enocean.protocol.constants import PACKET
from enocean.protocol.packet import Packet


class TCPClientCommunicator(Communicator):
    """socket communicator class for EnOcean radio over a raw TCP endpoint.

    Designed for ser2net-hosted dongles: the connection is **self-healing**.
    If the remote end is down / the socket breaks, we wait and reconnect with
    exponential backoff (1 -> 30 s) instead of stopping the thread, so the
    gateway keeps running and the web UI stays available. Only ``stop()``
    (explicit shutdown) terminates the loop.
    """
    logger = logging.getLogger('enocean.communicators.TCPClientCommunicator')

    #: backoff bounds (seconds) between reconnect attempts
    RECONNECT_MIN_DELAY = 1.0
    RECONNECT_MAX_DELAY = 30.0

    def __init__(self, host='', port=9637):
        super().__init__()
        self.host = host
        self.port = port
        self._sock = None

    @property
    def sock(self):
        return self._sock

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((self.host, self.port))
        s.settimeout(0.5)
        self._sock = s
        self.logger.info('TCPClientCommunicator connected to %s:%s', self.host, self.port)

    def run(self):
        self.logger.info('TCPClientCommunicator started')
        delay = self.RECONNECT_MIN_DELAY
        while not self._stop_flag.is_set():
            if self._sock is None:
                try:
                    self._connect()
                    delay = self.RECONNECT_MIN_DELAY  # connected -> reset backoff
                except Exception as e:   # pylint: disable=broad-except
                    self.logger.error('Exception occurred while connecting: %s', e)
                    self.logger.warning('retrying %s:%s in %.0fs', self.host, self.port, delay)
                    self._sock = None
                    # wait (in small steps so stop() still interrupts quickly)
                    waited = 0.0
                    while waited < delay and not self._stop_flag.is_set():
                        time.sleep(0.2)
                        waited += 0.2
                    delay = min(delay * 2, self.RECONNECT_MAX_DELAY)
                    continue

            pinged = time.time()
            try:
                # flush the transmit queue
                while True:
                    packet = self._get_from_send_queue()
                    if not packet:
                        break
                    try:
                        self._sock.send(bytearray(packet.build()))
                        pinged = time.time()
                    except Exception as e:   # pylint: disable=broad-except
                        self.logger.error('Exception occurred while sending: %s', e)
                        raise ConnectionError('send failed') from e

                try:
                    self._buffer.extend(bytearray(self._sock.recv(16)))
                    self.parse()
                except socket.timeout:
                    pass
                except ConnectionResetError as e:
                    self.logger.error('Exception occurred while recv: %s', e)
                    raise ConnectionError('connection reset') from e
                except Exception as e:   # pylint: disable=broad-except
                    self.logger.error('Exception occurred while parsing: %s', e)
                    raise

                # keep the connection alive with a common-command ping
                if time.time() > pinged + 30:
                    self.send(Packet(PACKET.COMMON_COMMAND, data=[0x08]))
                    pinged = time.time()
            except Exception as e:   # pylint: disable=broad-except
                # socket broke -> tear down and reconnect with backoff
                self.logger.warning('connection lost (%s); reconnecting with backoff', e)
                try:
                    self._sock.close()
                except Exception:   # pylint: disable=broad-except
                    pass
                self._sock = None
                delay = min(delay * 2, self.RECONNECT_MAX_DELAY)
                waited = 0.0
                while waited < delay and not self._stop_flag.is_set():
                    time.sleep(0.2)
                    waited += 0.2
                continue

        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:   # pylint: disable=broad-except
                pass
            self._sock = None
        self.logger.info('TCPClientCommunicator stopped')

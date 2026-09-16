"""Discover local EnOcean dongles (serial) and network ser2net endpoints (mDNS).

Local serial
------------
pyserial ``list_ports`` is used to enumerate the host's serial devices. An
EnOcean transceiver (USB300, TCM300/TCM310/TCM515/TCM516, TCM310) is usually
recognised by its description / USB VID:PID; we also flag any
``/dev/ttyUSB*`` / ``/dev/ttyACM*`` as a *candidate* so a user can pick it in
the config UI.

Network (mDNS)
--------------
busware / Eltako / other vendors advertise an EnOcean-to-Ethernet gateway or
ser2net endpoint over mDNS. Common service names in the wild:

  * ``_tcm515._tcp.local.``     - busware S300/S600 (TCM515 module)
  * ``_tcm310._tcp.local.``     - busware S300 (older TCM310)
  * ``_ser2net._tcp.local.``    - generic ser2net bridge (e.g. ser2net on a
                                  gateway / Raspberry Pi exposes the dongle)
  * ``_enocean._tcp.local.``    - generic EnOcean gateway service
  * ``_tcp._tcp.local.``        - fallback: report any TCP service whose TXT
                                  mentions enocean/ser2net/tcm

The browser sends a standard mDNS PTR query for each service type and parses
the answers (pointer + SRV + A/AAAA + TXT) using only stdlib ``socket`` -
no zeroconf dependency.
"""
from __future__ import annotations

import socket
import struct
import time

#: service types we probe (lowercase, no trailing .local)
SERVICE_TYPES = ("_tcm515._tcp", "_tcm310._tcp", "_ser2net._tcp",
                 "_enocean._tcp", "_enocangw._tcp")

#: keywords in a mDNS TXT record / service name that flag an EnOcean endpoint
_ENOCEAN_HINTS = (b"tcm515", b"tcm310", b"tcm300", b"enocean", b"ser2net",
                  b"usb300", b"tcm", b"eltako", b"fsb", b"fsr")

#: USB VID/PID (or substrings in hwid/description) of common EnOcean dongles
_ENOCEAN_VIDS = ("10:20", "0403", "1a86", "067b")  # qingping? no - see below
_ENOCEAN_HWID_HINTS = ("10:20", "tcm", "enocean", "usb300", "2504", "10:20")


def discover_serial(timeout=1.0):
    """Return a list of local serial ports that are (or could be) EnOcean.

    Each entry: {'device': ..., 'description': ..., 'hwid': ...,
    'enocean': bool, 'candidate': bool}
    """
    out = []
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").lower()
            hwid = (p.hwid or "").lower()
            device = p.device
            is_enocean = any(h in hwid or h in desc for h in _ENOCEAN_HWID_HINTS)
            is_candidate = any(device.startswith(x) for x in ("/dev/ttyUSB", "/dev/ttyACM"))
            out.append({
                'device': device,
                'description': p.description,
                'hwid': p.hwid,
                'enocean': bool(is_enocean),
                'candidate': bool(is_candidate or is_enocean),
            })
    except Exception:   # pylint: disable=broad-except
        pass
    return out


class _MdnsResponse:
    """minimal mDNS response parser (PTR/SRV/A/AAAA/TXT records)."""

    def __init__(self, data):
        self.data = data
        self.answers = []

    def _name(self, off, _depth=0):
        if _depth > 12:   # guard against pointer loops
            return "", off
        parts = []
        data = self.data
        while off < len(data):
            ln = data[off]
            if ln == 0:
                off += 1
                break
            if ln & 0xC0 == 0xC0:  # compressed pointer
                ptr = struct.unpack('!H', data[off:off + 2])[0] & 0x3FFF
                parts.append(self._name(ptr, _depth + 1)[0])
                off += 2
                break
            if ln >= 64:  # reserved label type
                off += 1
                continue
            if off + 1 + ln > len(data):
                break
            parts.append(data[off + 1:off + 1 + ln].decode('utf-8', 'replace'))
            off += 1 + ln
        return ".".join(parts), off

    def parse(self):
        data = self.data
        if len(data) < 12:
            return
        # questions
        qd = struct.unpack('!H', data[4:6])[0]
        off = 12
        for _ in range(qd):
            _, off = self._name(off)
            off += 4
        an = struct.unpack('!H', data[6:8])[0]
        for _ in range(an):
            name, off = self._name(off)
            rtype, rclass, ttl, rdlen = struct.unpack('!HHIH', data[off:off + 10])
            off += 10
            rdata = data[off:off + rdlen]
            off += rdlen
            self.answers.append((name, rtype, rclass, ttl, rdata))
        return self.answers


def discover_mdns(timeout=2.0):
    """Browse the configured mDNS service types; return discovered endpoints.

    Each entry: {'service': ..., 'name': ..., 'host': ..., 'port': int,
    'txt': {...}}
    """
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.2)
        try:
            s.bind(('', 5353))
        except OSError:
            pass  # bind may fail if another mdns client is bound - still send

        # send a PTR query for each service type
        for stype in SERVICE_TYPES:
            qname = (stype + ".local.").encode()
            # header: id=0 flags=0 qd=1 an=0 ... ; question: name, PTR(12), IN(1)
            question = qname + struct.pack('!HH', 12, 1)
            header = struct.pack('!HHHHHH', 0, 0, 1, 0, 0, 0)
            try:
                s.sendto(header + question, ("224.0.0.251", 5353))
            except OSError:
                continue

        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            resp = _MdnsResponse(data)
            try:
                answers = resp.parse()
            except Exception:   # pylint: disable=broad-except
                continue
            for name, rtype, _rclass, _ttl, rdata in (answers or []):
                if rtype == 12:  # PTR -> points at instance "host._type.local."
                    found.setdefault(name, {})
                    found[name]['instance'] = rdata.decode('utf-8', 'replace')
                elif rtype == 33:  # SRV: priority weight port target
                    port = struct.unpack('!H', rdata[4:6])[0]
                    found.setdefault(name, {})['port'] = port
                elif rtype == 16:  # TXT
                    txt = {}
                    i = 0
                    while i < len(rdata):
                        ln = rdata[i]
                        i += 1
                        kv = rdata[i:i + ln]
                        i += ln
                        if b'=' in kv:
                            k, _, v = kv.partition(b'=')
                            txt[k.decode('utf-8', 'replace')] = v.decode('utf-8', 'replace')
                    found.setdefault(name, {})['txt'] = txt
        s.close()
    except Exception:   # pylint: disable=broad-except
        return []

    # build the result list, only keeping services that look EnOcean-ish
    out = []
    for service, info in found.items():
        txt = info.get('txt', {})
        hints = b' '.join(k.encode() for k in txt.keys()) + b' ' + \
            (info.get('instance', '').encode())
        if any(h in hints.lower() for h in _ENOCEAN_HINTS) or \
           any(st in service for st in SERVICE_TYPES):
            out.append({
                'service': service,
                'name': info.get('instance', ''),
                'port': info.get('port'),
                'txt': txt,
            })
    return out


def discover(timeout=2.0):
    """return both local serial candidates and mDNS endpoints."""
    return {
        'serial': discover_serial(timeout),
        'mdns': discover_mdns(timeout),
    }

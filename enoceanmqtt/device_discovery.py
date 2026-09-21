"""Discover local EnOcean dongles (serial) and network ser2net endpoints (mDNS).

Local serial
------------
pyserial ``list_ports`` is used to enumerate the host's serial devices. An
EnOcean transceiver (USB300, TCM300/TCM310/TCM515/TCM516, ...) is usually
recognised by its description / USB VID:PID; we also flag any
``/dev/ttyUSB*`` / ``/dev/ttyACM*`` as a *candidate* so a user can pick it
in the config UI.

Network (mDNS)
--------------
busware / Eltako / other vendors advertise an EnOcean-to-Ethernet gateway or
ser2net endpoint over mDNS. Common service names in the wild:

  * ``_tcm515._tcp.local.``     - busware S300/S600 (TCM515 module)
  * ``_tcm310._tcp.local.``     - busware S300 (older TCM310)
  * ``_ser2net._tcp.local.``    - generic ser2net bridge (e.g. ser2net on a
                                  gateway / Raspberry Pi exposes the dongle)
  * ``_enocean._tcp.local.``    - generic EnOcean gateway service
  * ``_enocangw._tcp.local.``   - Busware enocean-gateway service

The browser sends a standard mDNS PTR query for each service type and parses
the answers (PTR + SRV + A/AAAA + TXT) using only stdlib ``socket`` - no
zeroconf dependency.

Two sockets are used:
  * a *query* socket bound to an ephemeral port - mDNS queries MUST originate
    from a unicast source port, otherwise responders will drop the reply
    (RFC 6762 source address/port checks);
  * a *listener* socket bound to 5353 and joined to the 224.0.0.251 group so
    we also receive unsolicited announcements (plug-and-play) and replies
    that are broadcast to the group.
"""
from __future__ import annotations

import socket
import struct
import time

#: service types we probe (lowercase, no trailing .local)
SERVICE_TYPES = ("_tcm515._tcp", "_tcm310._tcp", "_ser2net._tcp",
                 "_enocean._tcp", "_enocangw._tcp")

#: mDNS multicast group (RFC 6762)
_MDNS_ADDR = "224.0.0.251"
_MDNS_PORT = 5353
_MDNS_GROUP = socket.inet_aton(_MDNS_ADDR)

#: keywords in a mDNS TXT record / service name that flag an EnOcean endpoint
_ENOCEAN_HINTS = (b"tcm515", b"tcm310", b"tcm300", b"enocean", b"ser2net",
                  b"usb300", b"tcm", b"eltako", b"fsb", b"fsr", b"esp3",
                  b"tcpclientcommunicator")

#: USB VID/PID (or substrings in hwid/description) of common EnOcean dongles
_ENOCEAN_HWID_HINTS = ("10:20", "tcm", "enocean", "usb300", "2504")

#: fallback port when an mDNS SRV record is missing (ser2net default)
_DEFAULT_SER2NET_PORT = 30000


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
        """decode a (possibly compressed) name starting at byte offset *off*."""
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
            rdata_off = off
            off += rdlen
            self.answers.append((name, rtype, rclass, ttl, rdata, rdata_off))
        return self.answers


def _make_query(stype):
    """build a standard mDNS PTR question for ``stype._tcp.local.``"""
    qname = (stype + ".local.").encode()
    # header: id=0 flags=0 qd=1 ; question: name, QTYPE=PTR(12), QCLASS=IN(1)
    question = qname + struct.pack('!HH', 12, 1)
    header = struct.pack('!HHHHHH', 0, 0, 1, 0, 0, 0)
    return header + question


def _parse_txt(rdata):
    """decode a TXT record into a dict (RFC 6763: length-prefixed k=v pairs)."""
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
        else:
            txt[kv.decode('utf-8', 'replace')] = ""
    return txt


def _looks_like_enocean(service, instance, txt):
    """True when a discovered service shares hints with EnOcean/ser2net."""
    hints = b' '.join(k.encode('utf-8', 'replace') for k in txt.keys()) + b' ' + \
        b' '.join(str(v).encode('utf-8', 'replace') for v in txt.values()) + \
        b' ' + instance.encode('utf-8', 'replace')
    hints = hints.lower()
    return (any(h in hints for h in _ENOCEAN_HINTS) or
            any(st in service for st in SERVICE_TYPES))


def _ip_from_srv_target(target):
    """resolve an SRV target name (may be FQDN or IP literal) to an IP str."""
    target = target.strip().rstrip('.')
    if not target:
        return None
    # already an IP literal?
    try:
        socket.inet_aton(target)
        return target
    except OSError:
        pass
    try:
        # mDNS name -> usually resolvable via the cache or the same mDNS
        tok = socket.getaddrinfo(target, None, socket.AF_INET,
                                 socket.SOCK_STREAM, socket.IPPROTO_TCP)
        if tok:
            return tok[0][4][0]
    except OSError:
        pass
    return None


def discover_mdns(timeout=2.0, query=True):
    """Browse the configured mDNS service types; return discovered endpoints.

    Each entry: {'service': ..., 'name': ..., 'host': ..., 'port': int,
    'txt': {...}}

    If *query* is False, only unsolicited multicast announcements are
    listened for (no PTR queries are sent); used by the background cache
    thread between explicit UI refreshes.
    """
    found = {}

    def collect(resp):
        try:
            answers = resp.parse()
        except Exception:   # pylint: disable=broad-except
            return
        for name, rtype, _rclass, _ttl, rdata, roff in (answers or []):
            if rtype == 12:          # PTR -> points at instance name (encoded)
                try:
                    inst, _ = resp._name(roff)
                except Exception:   # pylint: disable=broad-except
                    inst = rdata.decode('utf-8', 'replace')
                found.setdefault(name, {})['instance'] = inst
            elif rtype == 33:        # SRV: priority weight port target
                if len(rdata) >= 6:
                    port = struct.unpack('!H', rdata[4:6])[0]
                    found.setdefault(name, {})['port'] = port
                    # SRV target name lives in rdata[6:]; pointers inside it
                    # are relative to the whole message -> decode with context
                    try:
                        target, _ = resp._name(roff + 6)
                        found.setdefault(name, {})['target'] = target
                    except Exception:   # pylint: disable=broad-except
                        pass
            elif rtype == 16:        # TXT
                found.setdefault(name, {})['txt'] = _parse_txt(rdata)
            elif rtype == 1:         # A record -> hostname = name, ip = rdata
                found.setdefault("_host_" + name, {})['ip'] = socket.inet_ntoa(rdata[:4])

    # --- listener socket (joins the group; captures broadcast replies + ---
    # --- unsolicited announcements)                                        ---
    listener = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        listener.bind(("", _MDNS_PORT))
        listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            _MDNS_GROUP + socket.inet_aton("0.0.0.0"))
        listener.settimeout(0.1)
    except OSError:
        listener = None

    # --- query socket (ephemeral; source port must be unicast per RFC 6762) --
    qsock = None
    if query:
        qsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        qsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        qsock.bind(("", 0))
        try:
            for stype in SERVICE_TYPES:
                qsock.sendto(_make_query(stype), (_MDNS_ADDR, _MDNS_PORT))
        except OSError:
            pass
        qsock.settimeout(0.1)

    end = time.time() + timeout
    while time.time() < end:
        # listener first (broadcast replies + announcements)
        if listener is not None:
            try:
                data, _addr = listener.recvfrom(4096)
                collect(_MdnsResponse(data))
            except socket.timeout:
                pass
            except OSError:
                listener.close()
                listener = None
        # query socket (unicast replies)
        if qsock is not None:
            try:
                data, _addr = qsock.recvfrom(4096)
                collect(_MdnsResponse(data))
            except socket.timeout:
                pass
            except OSError:
                qsock.close()
                qsock = None
        if listener is None and qsock is None:
            break

    for s in (listener, qsock):
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    # --- assemble endpoints ---------------------------------------------------
    out = []
    # merge PTR-only records into their SRV record via the instance name:
    # a real endpoint needs a port, and SRV carries it.  The PTR answer for a
    # service type tells us the *instance* name; SRV/TXT answers are keyed by
    # that instance name.  Only instances that have both a host and a port are
    # usable by the TCP communicator, so filter on those.
    for service, info in found.items():
        if service.startswith("_host_"):
            continue
        txt = info.get('txt', {})
        instance = info.get('instance', '')
        port = info.get('port')
        if port is None:
            # PTR-only entry (no SRV yet) - not yet connectable: skip
            continue
        # The service/instance naming differs between record types; match the
        # SRV/TXT answers (keyed by full instance name) with the PTR's
        # instance string.
        if instance and service != instance:
            continue
        if not _looks_like_enocean(service, instance, txt):
            continue

        # host: SRV target may be a name or an IP; fall back to instance
        target = info.get('target', '')
        host = target
        if not host or target.endswith('.local'):
            ip = None
            for _k, v in found.items():
                if _k.startswith("_host_") and v.get('ip'):
                    ip = v['ip']
                    break
            if ip:
                host = ip
            elif target:
                host = _ip_from_srv_target(target)
            elif instance:
                host = instance.rsplit('.', 2)[0]
        out.append({
            'service': service,
            'name': instance,
            'host': host,
            'port': int(port),
            'txt': txt,
        })
    return out


def discover(timeout=2.0):
    """return both local serial candidates and mDNS endpoints."""
    return {
        'serial': discover_serial(timeout),
        'mdns': discover_mdns(timeout),
    }
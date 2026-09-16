"""EnOcean secure-telegram crypto primitives — VAES stream cipher, AES-CMAC, and SLF parsing.

Pure functions with no I/O, unit-tested against the *Security of EnOcean Radio Networks v3.02*
Annex A.4 known-answer vectors. Only **VAES** (the sole encryption type defined in v3.02) is
implemented; AES-CBC and 2-byte CMAC (old drafts / AN510) are intentionally absent. This module is
the P5 foundation (5a); the secure decode/teach-in/TX pipeline (5b–5d) builds on it.

References: v3.02 Ch. 6.1 (VAES), Ch. 6.2 (CMAC), Ch. 3.5 (SLF).
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives import cmac as _cmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from enoceanmqtt.crc8 import calc as _crc8

# §6.1.4 — the fixed VAES "variable init vector" (RLC is XORed into its high bytes per telegram).
VAES_INIT_VECTOR = bytes.fromhex("3410DE8F1ABA3EFF9F5A117172EACABD")


def _aes_ecb_encrypt(key: bytes, block: bytes) -> bytes:
    """One AES-128-ECB block encryption — the primitive VAES/CMAC build on (not a cipher mode).

    ECB here is only the raw single-block AES function used to build the VAES keystream (spec §6.1);
    data is never encrypted in ECB mode, so the pattern-leak weakness does not apply. CodeQL's
    py/weak-cryptographic-algorithm flags any modes.ECB() use — false positive here.
    """
    cipher = Cipher(algorithms.AES(key), modes.ECB())  # codeql[py/weak-cryptographic-algorithm]
    encryptor = cipher.encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=False))


def _wrap_rlc(rlc: int, size_bytes: int) -> int:
    """Rolling code reduced modulo its width — wraps to 0 at the max, never overflows (§3.5)."""
    return int(rlc) & ((1 << (8 * size_bytes)) - 1)


def rlc_to_bytes(rlc: int, size_bytes: int) -> bytes:
    """Rolling code as big-endian bytes of the given width (3 = 24-bit, 4 = 32-bit).

    The value is wrapped to the width first, so a rolled-over RLC encodes as 0 rather than raising
    ``OverflowError`` (the RLC counter is modular over its width, per Security v3.02 §3.5).
    """
    return _wrap_rlc(rlc, size_bytes).to_bytes(size_bytes, "big")


def vaes(key: bytes, rlc: bytes, data: bytes) -> bytes:
    """VAES stream cipher (§6.1). Encrypt and decrypt are identical (XOR with the keystream).

    ``key`` is the 16-byte pre-shared key; ``rlc`` the raw rolling-code bytes (3 or 4), placed in
    the high bytes of the init vector; ``data`` the plaintext (encrypt) or ciphertext (decrypt), any
    length. The keystream is always an AES **encrypt** of the (chained) init vector — even when
    decrypting — because VAES is a stream cipher.
    """
    iv0 = _xor(VAES_INIT_VECTOR, (rlc + b"\x00" * 16)[:16])
    out = bytearray()
    prev = None
    for i in range(0, len(data), 16):
        block = data[i : i + 16]
        aes_in = iv0 if prev is None else _xor(prev, iv0)  # chain: prev keystream XOR IV0
        keystream = _aes_ecb_encrypt(key, aes_in)
        prev = keystream
        out += _xor(block, keystream[: len(block)])  # truncated XOR for a short final block
    return bytes(out)


def cmac(key: bytes, msg: bytes, mac_len: int) -> bytes:
    """AES-CMAC (RFC 4493) over ``msg``, truncated to the ``mac_len`` most-significant bytes (§6.2).

    ``mac_len`` is 3 or 4. The CMAC input per §6.2.1 is ``RORG-S || ENCRYPTED_DATA || RLC`` (the
    full RLC, transmitted or not). RFC-4493 subkey derivation is done inside ``cryptography``.
    """
    ctx = _cmac.CMAC(algorithms.AES(key))
    ctx.update(msg)
    return ctx.finalize()[:mac_len]


@dataclass(frozen=True)
class Slf:
    """Parsed Security Level Format byte (§3.5)."""

    rlc_size: int  # full RLC width in bytes used for VAES/CMAC (0, 2 legacy, 3, or 4)
    rlc_tx: bool  # RLC transmitted in the telegram (explicit) vs. implicit (window-scanned)
    cmac_len: int  # MAC length in bytes (3 or 4; 0 = none/unsupported)
    vaes: bool  # ENC_TYPE is VAES (the only supported value)
    raw: int

    @property
    def supported(self) -> bool:
        """True if this SLF is something we can actually process (VAES + a real MAC + an RLC)."""
        return self.vaes and self.cmac_len in (3, 4) and self.rlc_size in (3, 4)


# v3.02 §3.5.1 RLC_TYPE (bits 7-5) → (full RLC size in bytes, transmitted?). The legacy A.5 split
# (RLC_ALGO bits7-6 + RLC_TX bit5) is the *same* 8 bits, so both interpretations are covered here.
_RLC_TYPE = {
    0b100: (3, False),  # 24-bit implicit (ultra-low-power)
    0b101: (3, True),  # 24-bit, transmitted
    0b110: (4, True),  # 32-bit, 24-bit transmitted (legacy-receiver compat)
    0b111: (4, True),  # 32-bit, transmitted (recommended)
    0b000: (0, False),  # legacy: no RLC
    0b010: (2, False),  # legacy: 16-bit implicit (deprecated)
    0b011: (2, True),  # legacy: 16-bit transmitted (deprecated)
    0b001: (2, True),  # legacy edge (RLC_ALGO=00, RLC_TX=1)
}
_CMAC_LEN = {0b00: 0, 0b01: 3, 0b10: 4, 0b11: 0}

# Default SLF for a hand-configured secure device that doesn't specify one: 0x8B = 24-bit implicit
# RLC + 3-byte CMAC + VAES — what real PTM215 switches use (teach-in-provisioned devices carry their
# own SLF, so this only applies to manual `security:` entries).
DEFAULT_SLF = 0x8B


def parse_slf(byte: int) -> Slf:
    """Parse an SLF byte per v3.02 (bit-compatible with the legacy A.5 encoding)."""
    rlc_size, rlc_tx = _RLC_TYPE.get((byte >> 5) & 0b111, (4, True))
    return Slf(
        rlc_size=rlc_size,
        rlc_tx=rlc_tx,
        cmac_len=_CMAC_LEN[(byte >> 3) & 0b11],
        vaes=(byte & 0b111) == 0b011,
        raw=byte,
    )


@dataclass
class SecureDevice:
    """Per-device secure state used to decrypt/verify received telegrams (§4.2, B.3).

    ``rlc`` is the last-accepted inbound rolling code, advanced in place on a successful decode (the
    caller persists it). ``rlc_window`` bounds the forward scan for implicit (non-transmitted) RLC.
    """

    key: bytes
    rlc: int
    rlc_size: int
    rlc_tx: bool
    cmac_len: int
    rlc_window: int = 128


def decrypt_telegram(dev: SecureDevice, rorg_s: int, wire: bytes):
    """Decrypt + verify a 0x30/0x31 secure telegram's DATA field (``enc [+ rlc] + cmac``).

    Returns ``(inner_rorg, inner_data)`` on success (inner_rorg is None for RORG-less 0x30 — the
    caller supplies the device's configured RORG), or ``None`` if no RLC in the window authenticates
    the CMAC. Advances ``dev.rlc`` past the accepted rolling code. Implicit RLC scans a forward
    window; explicit (transmitted) RLC is trusted once, and must be ≥ the stored value.
    """
    if dev.cmac_len == 0 or dev.rlc_size == 0 or len(wire) <= dev.cmac_len:
        return None
    mac_rx = wire[-dev.cmac_len :]
    body = wire[: -dev.cmac_len]  # enc [+ transmitted rlc]
    if dev.rlc_tx:
        if len(body) < dev.rlc_size:
            return None
        enc = body[: -dev.rlc_size]
        wire_rlc = int.from_bytes(body[-dev.rlc_size :], "big")
        candidates = [wire_rlc] if wire_rlc >= dev.rlc else []
    else:
        enc = body
        candidates = list(range(dev.rlc, dev.rlc + dev.rlc_window + 1))

    for rlc_try in candidates:
        rlc_bytes = rlc_to_bytes(rlc_try, dev.rlc_size)
        if cmac(dev.key, bytes([rorg_s]) + enc + rlc_bytes, dev.cmac_len) == mac_rx:
            dev.rlc = _wrap_rlc(rlc_try + 1, dev.rlc_size)  # resync: next expected RLC (wraps)
            dec = vaes(dev.key, rlc_bytes, enc)
            if not dec:
                return None
            if rorg_s == 0x31:  # SEC_ENCAPS: inner RORG is the first decrypted byte
                return dec[0], dec[1:]
            # SEC (0x30): RORG-less PTM/RPS — the payload is a nibble (mask the recovered byte)
            return None, bytes([dec[0] & 0x0F])
    return None


def encrypt_telegram(dev: SecureDevice, rorg: int, data: bytes) -> tuple[int, bytes]:
    """Encrypt + MAC a plaintext telegram into a secure (0x30/0x31) DATA field (§4.2, B.4).

    RPS/PTM (rorg 0xF6) becomes RORG-less 0x30 (payload nibble-masked); everything else becomes 0x31
    with the inner RORG encapsulated. Returns ``(rorg_s, wire)``; wire = ``enc [+ rlc] + cmac``.
    Advances ``dev.rlc`` (the outbound rolling code) after use; the caller persists it.
    """
    rlc_bytes = rlc_to_bytes(dev.rlc, dev.rlc_size)
    if rorg == 0xF6:  # RPS/PTM → RORG-less, single nibble
        rorg_s = 0x30
        enc = bytes([vaes(dev.key, rlc_bytes, bytes(data))[0] & 0x0F])
    else:
        rorg_s = 0x31
        enc = vaes(dev.key, rlc_bytes, bytes([rorg, *data]))
    mac = cmac(dev.key, bytes([rorg_s]) + enc + rlc_bytes, dev.cmac_len)
    wire = enc + (rlc_bytes if dev.rlc_tx else b"") + mac
    dev.rlc = _wrap_rlc(dev.rlc + 1, dev.rlc_size)  # advance, wrapping at the width max
    return rorg_s, wire


def psk_crc8(psk: bytes) -> int:
    """CRC8 (poly 0x07) self-check of a pre-shared key (spec Annex A.3)."""
    return _crc8(psk)


@dataclass
class TeachIn:
    """The device parameters carried by a secure teach-in (SEC_TI 0x35)."""

    info: int
    slf: int
    rlc: int
    key: bytes
    ptm: bool  # TEACH_IN_INFO TYPE bit — a PTM (switch) device
    bidirectional: bool
    psk_used: bool


# PK bytes carried in msg1 (msg2 carries the remaining 9); matches the spec A.4.2 split.
_PK1_LEN = 7
_TEACH_IN_INFO_MSG2 = 0x40  # IDX=0b01 header for the second teach-in telegram


def parse_teach_in(msg1: bytes, msg2: bytes, psk: bytes | None = None) -> TeachIn | None:
    """Reassemble a 2-telegram SEC_TI teach-in (§5.2 / A.4) into its device parameters.

    ``msg1``/``msg2`` are the DATA fields after the 0x35 RORG. ``msg1`` = INFO,SLF,RLC,PK-part1;
    ``msg2`` = INFO,PK-part2. If the PSK bit is set, RLC+PK are VAES-decrypted with ``psk``. Returns
    None if malformed or a PSK telegram arrives without a key.
    """
    if len(msg1) < 3 or len(msg2) < 1:
        return None
    info, slf_byte = msg1[0], msg1[1]
    slf = parse_slf(slf_byte)
    rlc_size = slf.rlc_size or 3
    psk_used = bool((info >> 3) & 1)
    ptm = bool((info >> 2) & 1)
    bidirectional = (not ptm) and (info & 0b11) == 0b01
    combined = bytes(msg1[2:]) + bytes(msg2[1:])  # RLC + PK1 + PK2 = RLC + PK(16)
    if psk_used:
        if psk is None:
            return None
        combined = vaes(psk, b"\x00" * rlc_size, combined)
    key = combined[rlc_size : rlc_size + 16]
    if len(key) != 16:
        return None
    return TeachIn(
        info=info,
        slf=slf_byte,
        rlc=int.from_bytes(combined[:rlc_size], "big"),
        key=bytes(key),
        ptm=ptm,
        bidirectional=bidirectional,
        psk_used=psk_used,
    )


def build_teach_in(
    info: int, slf: int, rlc: int, key: bytes, psk: bytes | None = None
) -> tuple[bytes, bytes]:
    """Build the two SEC_TI DATA fields for a teach-in (inverse of ``parse_teach_in``)."""
    rlc_size = parse_slf(slf).rlc_size or 3
    combined = rlc_to_bytes(rlc, rlc_size) + bytes(key)  # RLC + PK(16)
    if psk is not None:
        combined = vaes(psk, b"\x00" * rlc_size, combined)
    split = rlc_size + _PK1_LEN
    msg1 = bytes([info, slf]) + combined[:split]
    msg2 = bytes([_TEACH_IN_INFO_MSG2]) + combined[split:]
    return msg1, msg2

"""EnOcean secure crypto (VAES + AES-CMAC + SLF) — Security v3.02 Annex A.4 known-answer vectors."""

import pytest

from enoceanmqtt.security import (
    SecureDevice,
    build_teach_in,
    cmac,
    decrypt_telegram,
    encrypt_telegram,
    parse_slf,
    parse_teach_in,
    psk_crc8,
    rlc_to_bytes,
    vaes,
)


def test_rlc_to_bytes_widths():
    assert rlc_to_bytes(0xC0FFEE, 3) == bytes.fromhex("C0FFEE")
    assert rlc_to_bytes(0x01020304, 4) == bytes.fromhex("01020304")


# A.4.1 / A.4.2 share this pre-shared key ("EnOcean GmbH.13\0").
PK_1 = bytes.fromhex("456E4F6365616E20476D62482E313300")
PK_3 = bytes.fromhex("E50880CF67790D5D66AA7F3B7AD77A3F")


def test_vaes_a41_sec_r():
    # SEC_R (0x31): inner = RORG(0xA5) || DATA(0827FF80); RLC C0FFEE (24-bit).
    enc = vaes(PK_1, bytes.fromhex("C0FFEE"), bytes.fromhex("A50827FF80"))
    assert enc.hex().upper() == "3EEAC4A2DF"


def test_cmac_a41_sec_r():
    # CMAC input = RORG-S(0x31) || ENCRYPTED_DATA || RLC.
    mac = cmac(PK_1, bytes.fromhex("313EEAC4A2DFC0FFEE"), 3)
    assert mac.hex().upper() == "EAF20E"


def test_vaes_a42_ptm():
    # SEC (0x30) PTM: no inner RORG, DATA = 0x09. Raw keystream XOR -> 0xCE (the &0x0F nibble
    # mask to 0x0E is applied by the 0x30 PTM handler, not by VAES itself).
    enc = vaes(PK_1, bytes.fromhex("3E2D00"), bytes.fromhex("09"))
    assert enc.hex().upper() == "CE"


def test_cmac_a42_ptm():
    # CMAC over the *masked* enc byte (0x0E): RORG-S(0x30) || 0E || RLC.
    mac = cmac(PK_1, bytes.fromhex("300E3E2D00"), 3)
    assert mac.hex().upper() == "05E56D"


def test_vaes_a43_chained_multiblock():
    # Two-block VAES (31-byte inner): RORG 0xD1 (MSC) + DATA 00..1D; 32-bit RLC 01020304.
    inner = bytes([0xD1]) + bytes(range(0x00, 0x1E))
    enc = vaes(PK_3, bytes.fromhex("01020304"), inner)
    assert enc.hex().upper() == "BB17C17A05CAF5575DE208302FB572A0FD3A4434A41096F102E60DC20D777A"


def test_cmac_a43_chained_4byte():
    enc = bytes.fromhex("BB17C17A05CAF5575DE208302FB572A0FD3A4434A41096F102E60DC20D777A")
    mac = cmac(PK_3, bytes.fromhex("31") + enc + bytes.fromhex("01020304"), 4)
    assert mac.hex().upper() == "3B4C380F"


def test_vaes_is_symmetric_roundtrip():
    # VAES is a stream cipher: applying it twice with the same key+RLC returns the plaintext.
    rlc = bytes.fromhex("00ABCDEF")
    plain = bytes(range(20))  # >16 bytes → exercises block chaining
    enc = vaes(PK_1, rlc, plain)
    assert enc != plain
    assert vaes(PK_1, rlc, enc) == plain


def test_decrypt_a41_sec_encaps():
    # 0x31 (SEC_ENCAPS), implicit 24-bit RLC, 3-B CMAC: recover inner RORG 0xA5 + DATA 0827FF80.
    dev = SecureDevice(key=PK_1, rlc=0xC0FFEE, rlc_size=3, rlc_tx=False, cmac_len=3)
    wire = bytes.fromhex("3EEAC4A2DFEAF20E")  # enc + cmac (RLC not transmitted)
    result = decrypt_telegram(dev, 0x31, wire)
    assert result == (0xA5, bytes.fromhex("0827FF80"))
    assert dev.rlc == 0xC0FFEF  # advanced past the accepted RLC


def test_decrypt_a42_sec_ptm():
    # 0x30 (SEC, RORG-less PTM), implicit 24-bit RLC, 3-B CMAC: recover the nibble 0x09.
    dev = SecureDevice(key=PK_1, rlc=0x3E2D00, rlc_size=3, rlc_tx=False, cmac_len=3)
    wire = bytes.fromhex("0E05E56D")  # masked enc + cmac
    result = decrypt_telegram(dev, 0x30, wire)
    assert result == (None, b"\x09")  # RORG-less; caller supplies the device RORG (F6)


def test_decrypt_implicit_rlc_window_resync():
    # The device's stored RLC lags the sender's by a few counts; the window scan resyncs.
    dev = SecureDevice(key=PK_1, rlc=0xC0FFE9, rlc_size=3, rlc_tx=False, cmac_len=3)  # 5 behind
    wire = bytes.fromhex("3EEAC4A2DFEAF20E")  # authenticates at RLC C0FFEE
    assert decrypt_telegram(dev, 0x31, wire) == (0xA5, bytes.fromhex("0827FF80"))
    assert dev.rlc == 0xC0FFEF


def test_decrypt_rejects_tampered_cmac():
    dev = SecureDevice(key=PK_1, rlc=0xC0FFEE, rlc_size=3, rlc_tx=False, cmac_len=3)
    wire = bytes.fromhex("3EEAC4A2DFEAF20F")  # last CMAC byte flipped
    assert decrypt_telegram(dev, 0x31, wire) is None
    assert dev.rlc == 0xC0FFEE  # unchanged on failure


def test_decrypt_out_of_window_fails():
    dev = SecureDevice(key=PK_1, rlc=0xC00000, rlc_size=3, rlc_tx=False, cmac_len=3)  # far behind
    wire = bytes.fromhex("3EEAC4A2DFEAF20E")
    assert decrypt_telegram(dev, 0x31, wire) is None


def test_encrypt_a41_sec_encaps():
    dev = SecureDevice(key=PK_1, rlc=0xC0FFEE, rlc_size=3, rlc_tx=False, cmac_len=3)
    rorg_s, wire = encrypt_telegram(dev, 0xA5, bytes.fromhex("0827FF80"))
    assert rorg_s == 0x31
    assert wire.hex().upper() == "3EEAC4A2DFEAF20E"  # enc + cmac (RLC not transmitted)
    assert dev.rlc == 0xC0FFEF  # outbound RLC advanced


def test_encrypt_a42_ptm():
    dev = SecureDevice(key=PK_1, rlc=0x3E2D00, rlc_size=3, rlc_tx=False, cmac_len=3)
    rorg_s, wire = encrypt_telegram(dev, 0xF6, bytes([0x09]))
    assert rorg_s == 0x30  # RORG-less PTM
    assert wire.hex().upper() == "0E05E56D"


def test_encrypt_decrypt_roundtrip_0x31():
    tx = SecureDevice(key=PK_1, rlc=0x001000, rlc_size=3, rlc_tx=False, cmac_len=4)
    rorg_s, wire = encrypt_telegram(tx, 0xA5, bytes.fromhex("DEADBEEF"))
    rx = SecureDevice(key=PK_1, rlc=0x001000, rlc_size=3, rlc_tx=False, cmac_len=4)
    assert decrypt_telegram(rx, rorg_s, wire) == (0xA5, bytes.fromhex("DEADBEEF"))


def test_encrypt_decrypt_roundtrip_0x30_ptm_explicit_rlc():
    tx = SecureDevice(key=PK_1, rlc=0x002000, rlc_size=3, rlc_tx=True, cmac_len=4)
    rorg_s, wire = encrypt_telegram(tx, 0xF6, bytes([0x05]))
    assert rorg_s == 0x30
    rx = SecureDevice(key=PK_1, rlc=0x002000, rlc_size=3, rlc_tx=True, cmac_len=4)
    assert decrypt_telegram(rx, rorg_s, wire) == (None, bytes([0x05]))


def test_rlc_to_bytes_wraps_at_width():
    # At/over the width max the RLC wraps modulo the width instead of raising OverflowError.
    assert rlc_to_bytes(0x1000000, 3) == bytes.fromhex("000000")  # 2^24 -> 0
    assert rlc_to_bytes(0x1000001, 3) == bytes.fromhex("000001")
    assert rlc_to_bytes(0x100000000, 4) == bytes.fromhex("00000000")  # 2^32 -> 0


def test_encrypt_telegram_rlc_rollover_wraps():
    # A 3-byte outbound RLC at its max encrypts fine and rolls over to 0 (no crash).
    dev = SecureDevice(key=PK_1, rlc=0xFFFFFF, rlc_size=3, rlc_tx=False, cmac_len=3)
    rorg_s, _wire = encrypt_telegram(dev, 0xA5, bytes.fromhex("0827FF80"))
    assert rorg_s == 0x31
    assert dev.rlc == 0  # wrapped, not 0x1000000


def test_encrypt_decrypt_roundtrip_across_rollover():
    tx = SecureDevice(key=PK_1, rlc=0xFFFFFF, rlc_size=3, rlc_tx=False, cmac_len=4)
    rorg_s, wire = encrypt_telegram(tx, 0xA5, bytes.fromhex("DEADBEEF"))
    rx = SecureDevice(key=PK_1, rlc=0xFFFFFF, rlc_size=3, rlc_tx=False, cmac_len=4)
    assert decrypt_telegram(rx, rorg_s, wire) == (0xA5, bytes.fromhex("DEADBEEF"))
    assert rx.rlc == 0  # resynced past the accepted RLC, wrapped


def test_teach_in_roundtrip_plain():
    key = bytes.fromhex("456E4F6365616E20476D62482E313300")
    msg1, msg2 = build_teach_in(info=0x24, slf=0x8B, rlc=0x3E2D00, key=key)
    ti = parse_teach_in(msg1, msg2)
    assert ti.key == key
    assert ti.rlc == 0x3E2D00
    assert ti.slf == 0x8B
    assert ti.ptm is True and ti.psk_used is False


def test_teach_in_roundtrip_psk():
    key = bytes.fromhex("456E4F6365616E20476D62482E313300")
    psk = bytes.fromhex("3410DE8F1ABA3EFF9F5A117172EACABD")
    msg1, msg2 = build_teach_in(info=0x2C, slf=0x8B, rlc=0x111111, key=key, psk=psk)  # 0x2C=PSK bit
    ti = parse_teach_in(msg1, msg2, psk=psk)
    assert ti.key == key and ti.rlc == 0x111111 and ti.psk_used is True
    assert parse_teach_in(msg1, msg2, psk=None) is None  # can't recover without the PSK


def test_psk_crc8_selfcheck():
    psk = bytes.fromhex("3410DE8F1ABA3EFF9F5A117172EACABD")
    assert psk_crc8(psk) == 0x07  # spec Annex A.3


@pytest.mark.parametrize(
    ("slf", "rlc_size", "rlc_tx", "cmac_len", "vaes_"),
    [
        (0xF3, 4, True, 4, True),  # recommended: 32-bit tx, 4-B CMAC, VAES
        (0xAB, 3, True, 3, True),  # energy-reduced: 24-bit tx, 3-B CMAC
        (0x8B, 3, False, 3, True),  # ultra-low-power: 24-bit implicit, 3-B CMAC
        (0xCB, 4, True, 3, True),  # 32-bit / 24-bit tx / 3-B CMAC
    ],
)
def test_parse_slf(slf, rlc_size, rlc_tx, cmac_len, vaes_):
    s = parse_slf(slf)
    assert (s.rlc_size, s.rlc_tx, s.cmac_len, s.vaes) == (rlc_size, rlc_tx, cmac_len, vaes_)
    assert s.supported

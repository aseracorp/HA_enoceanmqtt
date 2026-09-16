# Ported from t-ice/enocean-mqtt-ha (GPL-3.0) - the code-defined EEP engine.
# Validated against the official EnOcean certification vectors
# (tests/fixtures/certification/eep_certification.json).
"""Bit/byte helpers shared by the ESP3 packet layer and the EEP engine."""

from collections.abc import Iterable, Sequence


def combine_hex(data: Sequence[int]) -> int:
    """Combine a sequence of byte values into one big-endian integer."""
    output = 0x00
    for i, value in enumerate(reversed(data)):
        output |= value << i * 8
    return output


def int_to_bytes(value: int, length: int = 4) -> list[int]:
    """Split an integer into a big-endian list of *length* byte values (inverse of combine_hex)."""
    return [(value >> i * 8) & 0xFF for i in reversed(range(length))]


def format_address(value: int) -> str:
    """Format a 4-byte EnOcean address/sender id as 8 uppercase hex digits (no separators)."""
    return f"{value:08X}"


def to_bitarray(data: list[int] | bytearray | int, width: int = 8) -> list[bool]:
    """Convert bytes (list/bytearray) or an integer to a big-endian list of bits."""
    if isinstance(data, (list, bytearray)):
        data = combine_hex(data)
    return [digit == "1" for digit in bin(data)[2:].zfill(width)]


def from_bitarray(data: Iterable[bool]) -> int:
    """Convert a big-endian bit array back to an integer."""
    return int("".join(["1" if x else "0" for x in data]), 2)


def to_hex_string(data: int | Sequence[int]) -> str:
    """Format a byte (int) or a sequence of bytes as ':'-separated uppercase hex."""
    if isinstance(data, int):
        return f"{data:02X}"
    return ":".join(f"{o:02X}" for o in data)


def from_hex_string(hex_string: str) -> int | list[int]:
    """Parse ':'-separated hex into an int (single byte) or a list of ints."""
    reval = [int(x, 16) for x in hex_string.split(":")]
    if len(reval) == 1:
        return reval[0]
    return reval

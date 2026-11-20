"""Adds the standard Huffman tables to the MJPEG frames that lack them

A lot of UVC cameras leave the DHT segment out of their MJPEG frames.
Most decoders assume the standard tables from the JPEG specification
(ITU-T T.81, annex K.3) in that case, but some refuse to decode such an
image, for example Safari and everything else decoding through ImageIO"""
import struct

_DC_LUMINANCE = (
    bytes.fromhex("00010501010101010100000000000000"),
    bytes(range(12)),
)
_DC_CHROMINANCE = (
    bytes.fromhex("00030101010101010101010000000000"),
    bytes(range(12)),
)
_AC_LUMINANCE = (
    bytes.fromhex("0002010303020403050504040000017d"),
    bytes.fromhex(
        "01020300041105122131410613516107227114328191a1082342b1c1"
        "1552d1f02433627282090a161718191a25262728292a343536373839"
        "3a434445464748494a535455565758595a636465666768696a737475"
        "767778797a838485868788898a92939495969798999aa2a3a4a5a6a7"
        "a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8"
        "d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"),
)
_AC_CHROMINANCE = (
    bytes.fromhex("00020102040403040705040400010277"),
    bytes.fromhex(
        "0001020311040521310612415107617113223281081442"
        "91a1b1c109233352f0156272d10a162434e125f11718191a26"
        "2728292a35363738393a434445464748494a535455565758595a"
        "636465666768696a737475767778797a82838485868788898a92"
        "939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9ba"
        "c2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9"
        "eaf2f3f4f5f6f7f8f9fa"),
)

# The table class (DC = 0, AC = 1) is in the high nibble,
# the table ID (luminance = 0, chrominance = 1) in the low one
_TABLES = b"".join(
    bytes([table_info]) + code_counts + symbols
    for table_info, (code_counts, symbols) in (
        (0x00, _DC_LUMINANCE),
        (0x10, _AC_LUMINANCE),
        (0x01, _DC_CHROMINANCE),
        (0x11, _AC_CHROMINANCE),
    ))

SOI = b"\xff\xd8"
FILL_BYTE = 0xFF
DHT_MARKER = 0xC4
SOS_MARKER = 0xDA

# Every marker from C0 to CF starts a frame, except for DHT, JPG and DAC.
# The standard tables are only meant for the baseline and the extended
# sequential ones. UVC MJPEG is always baseline, the rest is left alone
SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {DHT_MARKER, 0xC8, 0xCC}
SEQUENTIAL_SOF_MARKERS = frozenset((0xC0, 0xC1))

DHT_SEGMENT = (bytes((0xFF, DHT_MARKER))
               + struct.pack(">H", len(_TABLES) + 2) + _TABLES)


def add_huffman_tables(data: bytes) -> bytes:
    """Returns the JPEG with the standard Huffman tables inserted in front
    of its scan. Data that already has the tables, that is not a baseline
    or extended sequential JPEG, or that is not a JPEG at all gets returned
    as is"""
    if data[:2] != SOI:
        return data
    position = 2
    while position + 4 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        # A marker can be padded with fill bytes. Cameras do not do that,
        # so rather than skipping them, the frame is returned untouched
        if marker == FILL_BYTE:
            return data
        if marker == DHT_MARKER:
            return data
        if marker in SOF_MARKERS and marker not in SEQUENTIAL_SOF_MARKERS:
            return data
        if marker == SOS_MARKER:
            # A single allocation, the frame gets copied only once
            return b"".join(
                (data[:position], DHT_SEGMENT, memoryview(data)[position:]))
        length, = struct.unpack(">H", data[position + 2:position + 4])
        position += 2 + length
    return data

"""Tests for adding the Huffman tables to MJPEG frames"""
import struct

import numpy as np
from turbojpeg import TurboJPEG  # type: ignore

from prusa.link.cameras.huffman import (  # type:ignore
    DHT_MARKER,
    DHT_SEGMENT,
    SOI,
    SOS_MARKER,
    add_huffman_tables,
)

EOI = b"\xff\xd9"
SCAN_DATA = b"\x12\x34\xff\x00\x56"


def segment(marker, payload):
    """Makes a JPEG segment"""
    length = struct.pack(">H", len(payload) + 2)
    return bytes((0xFF, marker)) + length + payload


APP0 = segment(0xE0, b"AVI1" + bytes(10))
DQT = segment(0xDB, bytes(65))
SOF0 = segment(0xC0, bytes(15))
SOF2 = segment(0xC2, bytes(15))
DHT = segment(DHT_MARKER, bytes(29))
SOS = segment(SOS_MARKER, bytes(10))

HEADER = SOI + APP0 + DQT + SOF0
SCAN = SOS + SCAN_DATA + EOI


def test_missing_tables():
    """The tables get inserted right in front of the scan"""
    assert add_huffman_tables(HEADER + SCAN) == HEADER + DHT_SEGMENT + SCAN


def test_present_tables():
    """A frame that has its own tables is not touched"""
    frame = HEADER + DHT + SCAN
    assert add_huffman_tables(frame) == frame


def test_added_only_once():
    """Running a fixed frame through again does not change it"""
    fixed = add_huffman_tables(HEADER + SCAN)
    assert add_huffman_tables(fixed) == fixed


def test_marker_in_scan_data():
    """A DHT marker in the entropy coded data does not count as a table"""
    scan = SOS + b"\xff\xc4" + SCAN_DATA + EOI
    assert add_huffman_tables(HEADER + scan) == HEADER + DHT_SEGMENT + scan


def test_fill_bytes():
    """A marker padded with a fill byte makes the frame pass untouched"""
    frame = HEADER + b"\xff" + SCAN
    assert add_huffman_tables(frame) == frame


def test_progressive_frame():
    """The standard tables are not added to a progressive JPEG"""
    frame = SOI + APP0 + DQT + SOF2 + SCAN
    assert add_huffman_tables(frame) == frame


def test_not_a_jpeg():
    """Other data passes through untouched"""
    for data in (b"", b"\xff", b"YUYV" * 8, SOI, SOI + APP0, HEADER):
        assert add_huffman_tables(data) == data


def test_table_segment():
    """The segment holds four tables that are sized the way they claim"""
    assert DHT_SEGMENT[:2] == b"\xff\xc4"
    length, = struct.unpack(">H", DHT_SEGMENT[2:4])
    assert length == len(DHT_SEGMENT) - 2

    table_infos = []
    position = 4
    while position < len(DHT_SEGMENT):
        table_infos.append(DHT_SEGMENT[position])
        code_counts = DHT_SEGMENT[position + 1:position + 17]
        symbols = DHT_SEGMENT[position + 17:position + 17 + sum(code_counts)]
        assert len(symbols) == sum(code_counts)
        assert len(set(symbols)) == len(symbols)
        position += 17 + len(symbols)
    assert position == len(DHT_SEGMENT)
    assert table_infos == [0x00, 0x10, 0x01, 0x11]


def strip_tables(data):
    """Removes the DHT segments in front of the first scan"""
    position = 2
    while data[position + 1] != SOS_MARKER:
        length, = struct.unpack(">H", data[position + 2:position + 4])
        end = position + 2 + length
        if data[position + 1] == DHT_MARKER:
            data = data[:position] + data[end:]
        else:
            position = end
    return data


def tables_in(data):
    """Returns the Huffman tables of all DHT segments in front of the first
    scan, keyed by their class and ID"""
    tables = {}
    position = 2
    while data[position + 1] != SOS_MARKER:
        length, = struct.unpack(">H", data[position + 2:position + 4])
        end = position + 2 + length
        if data[position + 1] == DHT_MARKER:
            table = position + 4
            while table < end:
                size = 17 + sum(data[table + 1:table + 17])
                tables[data[table]] = data[table + 1:table + size]
                table += size
        position = end
    return tables


def test_table_contents():
    """libjpeg-turbo writes the standard tables into the JPEGs it encodes.
    Ours have to be byte for byte the same, and a JPEG that gets its tables
    swapped for ours has to decode to the same pixels"""
    jpeg = TurboJPEG()
    rows, columns = np.mgrid[0:64, 0:96]
    noise = np.random.default_rng(1).integers(0, 64, (64, 96, 3))
    image = np.dstack((rows * 4, columns * 2, rows + columns)) + noise
    encoded = jpeg.encode(image.clip(0, 255).astype(np.uint8))

    assert tables_in(SOI + DHT_SEGMENT + SOS) == tables_in(encoded)

    stripped = strip_tables(encoded)
    assert not tables_in(stripped)
    assert add_huffman_tables(stripped) != stripped

    expected = jpeg.decode(encoded)
    assert np.array_equal(jpeg.decode(add_huffman_tables(stripped)),
                          expected)

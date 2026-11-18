"""Tests for adding the Huffman tables to MJPEG frames"""
import struct

from prusa.link.cameras.huffman import (  # type:ignore
    DHT_SEGMENT,
    add_huffman_tables,
)

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
SCAN_DATA = b"\x12\x34\xff\x00\x56"


def segment(marker, payload):
    """Makes a JPEG segment"""
    length = struct.pack(">H", len(payload) + 2)
    return bytes((0xFF, marker)) + length + payload


APP0 = segment(0xE0, b"AVI1" + bytes(10))
DQT = segment(0xDB, bytes(65))
SOF0 = segment(0xC0, bytes(15))
DHT = segment(0xC4, bytes(29))
SOS = segment(0xDA, bytes(10))

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

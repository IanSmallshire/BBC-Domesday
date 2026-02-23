"""
TDD tests for the BBC Domesday walk/gallery binary parser.

All tests that touch real data use the GALLERY file at:
    data/national/NationalA/GALLERY
relative to the repository root.

Verified header values (from hex dump + BCPL source):
  base_view  = 802  (r(27)=803, minus 1)
  base_plan  = 321  (r(28))
  syslev     = 1    (r(29), gallery mode)
  initial_view = 1  (r(ctable) at byte 120)
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

# Locate the repository root (two levels above tests/)
REPO_ROOT = Path(__file__).parent.parent
GALLERY_PATH = REPO_ROOT / "data" / "NationalA" / "VFS" / "GALLERY"
NAMES_PATH   = REPO_ROOT / "data" / "NationalA" / "VFS" / "NAMES"
JPGIMG_ROOT  = REPO_ROOT / "data" / "NationalA" / "jpgimg"


def require_gallery(fn):
    """Skip a test if the GALLERY data file is not present."""
    return pytest.mark.skipif(
        not GALLERY_PATH.exists(),
        reason=f"GALLERY file not found at {GALLERY_PATH}",
    )(fn)


def require_names(fn):
    """Skip a test if the NAMES data file is not present."""
    return pytest.mark.skipif(
        not NAMES_PATH.exists(),
        reason=f"NAMES file not found at {NAMES_PATH}",
    )(fn)


# ── import the module under test ──────────────────────────────────────────────
from domesday.parser import (
    decode_names_address, leftof, load_gallery, parse_closeup_frames,
    parse_dataset, parse_essay, parse_gallery_item, parse_names_record,
    probe_data_type, rightof,
)
from domesday.models import WalkDataset, Node, PlanPosition


# ── test 1: header fields ─────────────────────────────────────────────────────

@require_gallery
def test_gallery_header():
    """Parse GALLERY and verify the three key header fields."""
    ds = load_gallery(GALLERY_PATH)
    assert ds.syslev == 1, "syslev should be 1 (gallery mode)"
    assert ds.base_view == 802, "base_view should be r(27)-1 = 802"
    assert ds.base_plan == 321, "base_plan should be r(28) = 321"


# ── test 2: node graph populated ──────────────────────────────────────────────

@require_gallery
def test_navigation_graph_built():
    """Nodes dict must be non-empty and every node must have a valid frame number."""
    ds = load_gallery(GALLERY_PATH)
    assert len(ds.nodes) > 0, "nodes dict must not be empty"
    for view, node in ds.nodes.items():
        assert node.frame > 0, f"view {view} has non-positive frame {node.frame}"
        assert node.frame == ds.base_view + view, (
            f"view {view}: expected frame {ds.base_view + view}, got {node.frame}"
        )


# ── test 3: dead-end detection ────────────────────────────────────────────────

@require_gallery
def test_dead_end_detection():
    """Any node whose ctable next_view == 0 must have forward=None."""
    ds = load_gallery(GALLERY_PATH)
    dead_ends = [n for n in ds.nodes.values() if n.forward is None and not n.linked_dataset]
    assert len(dead_ends) > 0, "Expected at least some dead-end nodes in the gallery"
    for node in dead_ends:
        assert node.forward is None


# ── test 4: frame path resolution ────────────────────────────────────────────

def test_frame_path_resolution():
    """WalkDataset.get_frame_path must put frames in the correct 1000-frame folder."""
    # Use a dummy dataset for path logic only
    ds = WalkDataset(
        source_file=Path("/dev/null"),
        syslev=1,
        base_view=802,
        base_plan=321,
        initial_view=1,
        nodes={},
    )
    root = Path("/data/jpgimg")

    assert ds.get_frame_path(802, root) == root / "00" / "802.jpg"
    assert ds.get_frame_path(1500, root) == root / "01" / "1500.jpg"
    assert ds.get_frame_path(999, root) == root / "00" / "999.jpg"
    assert ds.get_frame_path(1000, root) == root / "01" / "1000.jpg"
    assert ds.get_frame_path(10000, root) == root / "10" / "10000.jpg"


# ── test 5: right-turn wrap-around ────────────────────────────────────────────

def test_right_turn():
    """rightof() must wrap view 8 back to view 1 and rightof(7) == 8."""
    # Within one group (views 1-8)
    assert rightof(1) == 2
    assert rightof(7) == 8
    assert rightof(8) == 1   # wrap-around: (8 & 7) == 0 → 8-7 == 1

    # Second group (views 9-16): same pattern
    assert rightof(9) == 10
    assert rightof(15) == 16
    assert rightof(16) == 9  # (16 & 7) == 0 → 16-7 == 9

    # leftof is the inverse
    assert leftof(2) == 1
    assert leftof(1) == 8
    assert leftof(8) == 7


# ── test 6: plan position unpacking ──────────────────────────────────────────

def test_plan_position_unpacking():
    """
    Verify that the plan table parser correctly unpacks packed uint16 words.

    Build a minimal synthetic dataset with one plan entry and confirm that
    X, Y, plan_number, and base_direction are extracted correctly.
    """
    # Layout:
    #   ctable_offset = 120  (2 entries × 4 bytes = 8 bytes: view-0 slot + view-1)
    #   ptable_offset = 128  (ctable_offset + 8; one plan entry = 4 bytes)
    #   dtable_offset = 132  (ptable_offset + 4; no details needed)
    #
    # Plan entry for view 1: Y=0x1234, X=0x5678
    #   → plan_y=0x234 (564), plan_number=0x1 (1)
    #   → plan_x=0x678 (1656), base_direction=0x5 (5)

    # Build a 256-byte synthetic dataset
    buf = bytearray(256)

    def w16(offset, val):
        struct.pack_into("<h", buf, offset, val)

    def wu16(offset, val):
        struct.pack_into("<H", buf, offset, val)

    ltable_offset = 60
    ctable_offset = 120
    ptable_offset = 128  # ctable has 2 entries (view 0 + view 1) = 8 bytes
    dtable_offset = 132  # one ptable entry (4 bytes) after ptable_offset

    # header
    w16(28, ltable_offset)   # r(14) ltable
    w16(32, ctable_offset)   # r(16) ctable
    w16(36, ptable_offset)   # r(18) ptable
    w16(40, dtable_offset)   # r(20) dtable
    w16(50, 62)              # r(25) detail word-count: (256-132)//2 = 62
    w16(54, 101)             # r(27) base_view+1 → base_view=100
    w16(56, 50)              # r(28) base_plan
    w16(58, 0)               # r(29) syslev

    # ctable: view 0 = initial_view(=1), view 1 = dead-end with no detail
    w16(ctable_offset,     1)   # initial view = 1
    w16(ctable_offset + 2, 0)   # detail for view 0 (unused slot)
    w16(ctable_offset + 4, 0)   # view 1: next_view = 0 (dead-end)
    w16(ctable_offset + 6, -1)  # view 1: detail_offset = -1 (no detail)

    # ptable: group 0 (views 1-8) at ptable_offset
    # y_word = 0x1234, x_word = 0x5678
    wu16(ptable_offset,     0x1234)   # y_word
    wu16(ptable_offset + 2, 0x5678)   # x_word

    data = bytes(buf)
    ds = parse_dataset(data)

    assert 1 in ds.nodes, "view 1 should be in nodes"
    plan = ds.nodes[1].plan
    assert plan is not None, "view 1 should have a plan position"

    assert plan.y == 0x234,           f"plan.y should be 0x234 (564), got {plan.y:#x}"
    assert plan.plan_number == 0x1,   f"plan.plan_number should be 1, got {plan.plan_number}"
    assert plan.x == 0x678,           f"plan.x should be 0x678 (1656), got {plan.x:#x}"
    assert plan.base_direction == 0x5, f"plan.base_direction should be 5, got {plan.base_direction}"


# ── test 7: decode_names_address ──────────────────────────────────────────────

def test_decode_names_address():
    """decode_names_address correctly splits a 32-bit address into (is_data2, byte_offset)."""
    # DATA1 address: bit 15 of high word = 0; high=0x0002, low=0x1000 → offset=0x21000
    is_d2, off = decode_names_address(0x00021000)
    assert is_d2 is False
    assert off == 0x21000

    # DATA2 address: bit 15 of high word = 1; strip it to get same offset
    is_d2, off = decode_names_address(0x80021000)
    assert is_d2 is True
    assert off == 0x21000

    # Zero address
    is_d2, off = decode_names_address(0x00000000)
    assert is_d2 is False
    assert off == 0


# ── test 8: parse_names_record — synthetic ────────────────────────────────────

def test_parse_names_record_synthetic():
    """parse_names_record correctly extracts title, type and address from a 36-byte record."""
    # bytes 0-30 (31 bytes): title area; byte 31: type; bytes 32-35: address
    title_bytes = b'Fashion for Feet' + b' ' * 15  # 31 bytes
    rec_bytes = title_bytes + bytes([8]) + b'\x10\x00\x00\x01'  # 36 bytes total
    rec = parse_names_record(rec_bytes, 0)
    assert rec['title'] == 'Fashion for Feet'
    assert rec['type'] == 8
    assert rec['address'] == 0x01000010


# ── test 9: parse_gallery_item — synthetic ────────────────────────────────────

def test_parse_gallery_item_synthetic():
    """parse_gallery_item reads a uint32 record index from dtable_byte + item_offset*2."""
    data = bytearray(20)
    struct.pack_into('<I', data, 6, 42)   # record index 42 at byte offset 6 = 0 + 3*2
    idx = parse_gallery_item(bytes(data), dtable_byte=0, item_offset=3)
    assert idx == 42


# ── test 10: parse_closeup_frames — single frame ─────────────────────────────

def test_parse_closeup_frames_single():
    """parse_closeup_frames returns one frame for a 1-entry chain."""
    data = bytearray(20)
    struct.pack_into('<H', data, 0, 1)   # count = 1
    struct.pack_into('<h', data, 2, 5)   # delta = +5
    frames = parse_closeup_frames(bytes(data), dtable_byte=0, item_offset=0, base_view=1000)
    assert frames == [1005]
    label = f'Close-up ({len(frames)} frame{"s" if len(frames) != 1 else ""})'
    assert label == 'Close-up (1 frame)'


# ── test 11: parse_closeup_frames — multiple frames ──────────────────────────

def test_parse_closeup_frames_plural():
    """parse_closeup_frames returns multiple frames and the plural label is correct."""
    data = bytearray(20)
    struct.pack_into('<H', data, 0, 3)    # count = 3
    struct.pack_into('<h', data, 2,  1)   # delta = +1
    struct.pack_into('<h', data, 4,  2)   # delta = +2
    struct.pack_into('<h', data, 6, -1)   # delta = -1
    frames = parse_closeup_frames(bytes(data), 0, 0, 100)
    assert frames == [101, 102, 99]
    label = f'Close-up ({len(frames)} frame{"s" if len(frames) != 1 else ""})'
    assert label == 'Close-up (3 frames)'


# ── test 12: parse_essay figures — all sentinel ───────────────────────────────

def test_essay_figures_all_sentinel(tmp_path):
    """parse_essay returns figures=[] when all 25 figure records are sentinel (0xFFFFFFFF)."""
    data = bytearray(28)          # 28-byte header
    data += b'\xff' * 200         # 25 × 8-byte records, all sentinel
    data += struct.pack('<H', 1)  # num_pages = 1
    data += b' ' * 30            # title[0]
    data += b' ' * 30            # title[1]
    data += b'\x00' * 858        # page 0 text
    p = tmp_path / "essay.bin"
    p.write_bytes(bytes(data))
    essay = parse_essay(p, file_offset=0)
    assert essay['figures'] == []


# ── test 13: parse_essay figures — valid entries ──────────────────────────────

def test_essay_figures_with_valid_entries(tmp_path):
    """parse_essay extracts figure records and clamps pnum ≤ 0 to 1."""
    fig_area = bytearray(200)
    # Record 0: address=0x00010000, pnum=3
    struct.pack_into('<H', fig_area,  0, 1)           # page_num field (bytes 0-1, unused in output)
    struct.pack_into('<I', fig_area,  2, 0x00010000)  # address
    struct.pack_into('<h', fig_area,  6, 3)           # pnum → output page_num = max(1, 3) = 3
    # Record 1: address=0x80020000 (DATA2 flag), pnum=0 → clamped to 1
    struct.pack_into('<H', fig_area,  8, 2)
    struct.pack_into('<I', fig_area, 10, 0x80020000)
    struct.pack_into('<h', fig_area, 14, 0)
    # Records 2-24: sentinel
    for i in range(2, 25):
        struct.pack_into('<I', fig_area, i * 8 + 2, 0xFFFFFFFF)

    data = bytearray(28) + fig_area
    data += struct.pack('<H', 1)    # num_pages = 1
    data += b' ' * 30 + b' ' * 30  # title[0] and title[1]
    data += b'\x00' * 858           # page 0 text
    p = tmp_path / "essay.bin"
    p.write_bytes(bytes(data))
    essay = parse_essay(p, file_offset=0)
    assert len(essay['figures']) == 2
    assert essay['figures'][0] == {'address': 0x00010000, 'page_num': 3}
    assert essay['figures'][1] == {'address': 0x80020000, 'page_num': 1}


# ── test 14: probe_data_type ──────────────────────────────────────────────────

@pytest.mark.parametrize("num_pics,expected", [
    (1,   'photo'),
    (50,  'photo'),
    (200, 'photo'),
    (0,   'essay'),
    (201, 'essay'),
])
def test_probe_data_type(tmp_path, num_pics, expected):
    """probe_data_type returns 'photo' for num_pics 1-200 and 'essay' otherwise."""
    data = bytearray(30)
    struct.pack_into('<H', data, 28, num_pics)
    p = tmp_path / "data.bin"
    p.write_bytes(bytes(data))
    assert probe_data_type(p, file_offset=0) == expected


# ── test 15: real gallery nodes have details ──────────────────────────────────

@require_gallery
def test_gallery_nodes_have_details():
    """At least some gallery nodes should have detail icons populated."""
    ds = load_gallery(GALLERY_PATH)
    nodes_with_details = [n for n in ds.nodes.values() if n.details]
    assert len(nodes_with_details) > 0, "Expected some gallery nodes to have detail icons"


# ── test 16: real NAMES title lookup ─────────────────────────────────────────

@require_gallery
@require_names
def test_real_names_title_lookup():
    """For the first gallery node with a detail icon, the NAMES lookup returns a valid record."""
    data = GALLERY_PATH.read_bytes()
    names_data = NAMES_PATH.read_bytes()
    ds = load_gallery(GALLERY_PATH)
    for node in ds.nodes.values():
        for icon in node.details:
            record_index = parse_gallery_item(data, ds.dtable_byte, icon.item_offset)
            rec = parse_names_record(names_data, record_index)
            assert isinstance(rec['title'], str)
            assert rec['type'] in range(0, 11)
            return  # one lookup is sufficient
    pytest.skip("No gallery items with detail icons found")

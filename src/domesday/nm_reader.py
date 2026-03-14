"""BBC Domesday NM (National Mappable) dataset reader and PNG renderer.

Generalises the LIMESTONE-specific nm_heatmap.py to work with any NM type 1
dataset at any bbox.

ADF access: byte_offset = sector_number × 256
Frame N starts at ADF byte N × 6144 (24 sectors × 256 bytes).

Parse chain (verified against BCPL source NM/load1.b, load2.b, subsets.b):
    NAMES record sector_addr → dataset header (byte 1 = type)
    → sub-dataset index at frame_off+150 within dataset frame
    → raster sub-dataset header (at abs_record / word_ofs×2)
    → coarse index at data_base_frame / data_byte_offset
    → fine index → RLE fine blocks

Dataset header layout (at sector_addr × 256, i.e. frame_off within dataset frame):
  +1:      dataset_type (byte)
  +2..+13: 3 × 4-byte addresses (private/desc/tech text)
  +14..+65: skip 54 bytes (thesaurus ptrs, title)
  +66..+105: primary units string (40 bytes)
  +106..+145: secondary units string (40 bytes)
  +146..+147: (advance 2)
  +148..+149: value_data_type (u16)
  +150: sub-dataset index starts here
    u16: num_subsets
    per entry (6 bytes): s16 key, u16 rel_rec, s16 word_ofs
    sub_frame = dataset_frame + rel_rec
    sub_byte_offset = word_ofs × 2
    key = resolution in km (verified from NN/display1.b choose.res)

Sub-dataset header at {sub_frame, sub_byte_offset}:
  +0..+1:  data_record_no (u16, relative) → data_base_frame = dataset_frame + data_record_no
  +2..+3:  data_word_offset (u16) → data_byte_offset = data_word_offset × 2
  +4..+5:  gr_start_e (u16, 100m units)
  +6..+7:  gr_start_n (u16, 100m units)
  +8..+9:  gr_end_e (u16, 100m units)
  +10..+11: gr_end_n (u16, 100m units)
  +12..+13: primary_norm_factor (s16)
  +14..+15: secondary_norm_factor (s16)
  +16: data_size (byte: 1=uint8, 2=uint16, 4=uint32)
  +17: num_default_ranges (byte)
  +18+: num_default_ranges × 4-byte cut points (one get.size4.value each)

Coarse index at {data_base_frame, data_byte_offset}:
  +0..+1: num_we_blocks (u16)
  +2..+3: num_sn_blocks (u16)
  +4 + ci×4: entry ci → (u16 coarse_rec, u16 coarse_ofs)
  fi_frame = data_base_frame + coarse_rec - 1
  fi_byte  = (coarse_ofs - 1) × 2

Fine index (16 entries, guaranteed within one frame):
  Entry k: (u16 fine_rec, u16 fine_ofs)
  fb_frame = data_base_frame + fine_rec - 1
  fb_byte  = (fine_ofs - 1) × 2

Fine block: RLE-encoded, up to 64 items (8×8 km² each at 1 km² resolution).
"""

from __future__ import annotations

import colorsys
import io
import re
import struct
from pathlib import Path
from typing import BinaryIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# ADF / frame constants
# ---------------------------------------------------------------------------

SECTOR_SIZE = 256
SECTORS_PER_FRAME = 24
BYTES_PER_FRAME = SECTOR_SIZE * SECTORS_PER_FRAME   # 6144

UNIFORM_MISSING = 0x8000

# Byte offset of the sub-dataset index relative to the start of the dataset
# header within its frame.  Verified by tracing through NM/load1.b and
# NM/subsets.b: 2 + 12 + 54 + 40 + 40 + 2 = 150 bytes.
_SUB_DATASET_INDEX_OFFSET = 150


# ---------------------------------------------------------------------------
# Low-level ADF helpers
# ---------------------------------------------------------------------------

def read_frame(adf_file: BinaryIO, frame_no: int) -> bytes:
    """Read one 6144-byte frame from the open ADF file."""
    adf_file.seek(frame_no * BYTES_PER_FRAME)
    return adf_file.read(BYTES_PER_FRAME)


def read_cross_frame(cached_frame_fn, frame_no: int, byte_off: int, length: int) -> bytes:
    """Read `length` bytes from the ADF, transparently crossing frame boundaries.

    The BCPL runtime uses g.nm.inc.frame.ptr to handle frame crossings
    automatically for every 16-bit word read.  This function provides the
    same transparency for a contiguous byte range.
    """
    # Fast path: entirely within one frame (the common case)
    if byte_off + length <= BYTES_PER_FRAME:
        return cached_frame_fn(frame_no)[byte_off: byte_off + length]
    result = bytearray()
    while len(result) < length:
        frame = cached_frame_fn(frame_no)
        take = min(BYTES_PER_FRAME - byte_off, length - len(result))
        result.extend(frame[byte_off: byte_off + take])
        frame_no += 1
        byte_off = 0
    return bytes(result)


def u16(buf: bytes, off: int) -> int:
    return (buf[off + 1] << 8) | buf[off]


def s16(buf: bytes, off: int) -> int:
    v = u16(buf, off)
    return v - 65536 if v >= 32768 else v


def get_record_number(buf: bytes, off: int) -> int:
    """nm.get.record.number: preserve 0x8000 (uniform missing), else sign-extend."""
    v = u16(buf, off)
    if v == UNIFORM_MISSING:
        return UNIFORM_MISSING
    return v - 65536 if v >= 32768 else v


# ---------------------------------------------------------------------------
# Dataset header + sub-dataset index parsing
# ---------------------------------------------------------------------------

def _sector_to_frame_and_offset(sector_addr: int) -> tuple[int, int]:
    """Return (frame_no, frame_byte_offset) for a sector address."""
    byte_offset = sector_addr * SECTOR_SIZE
    frame_no = byte_offset // BYTES_PER_FRAME
    frame_off = byte_offset % BYTES_PER_FRAME
    return frame_no, frame_off


def parse_dataset_header(adf_file: BinaryIO, sector_addr: int) -> dict:
    """Read the dataset header at sector_addr × 256.

    Returns:
        dataset_type: int (1 = grid mappable, 2 = areal, 3 = areal boundary)
        dataset_frame: int — frame number of the header
        frame_off: int — byte offset within that frame
        index_offset: int — byte offset of sub-dataset index within frame
                            = frame_off + _SUB_DATASET_INDEX_OFFSET
    """
    frame_no, frame_off = _sector_to_frame_and_offset(sector_addr)
    frame = read_frame(adf_file, frame_no)

    dataset_type = frame[frame_off + 1]

    return {
        "dataset_type": dataset_type,
        "dataset_frame": frame_no,
        "frame_off": frame_off,
        "index_offset": frame_off + _SUB_DATASET_INDEX_OFFSET,
    }


def parse_sub_dataset_index(adf_file: BinaryIO, dataset_frame: int, index_offset: int) -> list[dict]:
    """Read the sub-dataset index at byte `index_offset` within `dataset_frame`.

    Index layout (all u16 LE):
      u16: num_subsets
      Per entry (6 bytes each):
        s16 key, u16 rel_rec, s16 word_ofs
    """
    frame = read_frame(adf_file, dataset_frame)

    # Handle case where index_offset is past the end of the frame (unusual)
    if index_offset >= BYTES_PER_FRAME - 2:
        return []

    num_subsets = u16(frame, index_offset)
    if num_subsets <= 0 or num_subsets > 256:
        return []

    entries = []
    off = index_offset + 2
    frame_no = dataset_frame

    for _ in range(num_subsets):
        if off + 6 > BYTES_PER_FRAME:
            # Advance to next frame (g.nm.inc.frame.ptr handles this)
            frame_no += 1
            frame = read_frame(adf_file, frame_no)
            off = 0
        key      = s16(frame, off)
        rel_rec  = u16(frame, off + 2)
        word_ofs = s16(frame, off + 4)
        off += 6
        entries.append({
            "key": key,
            "rel_rec": rel_rec,
            "word_ofs": word_ofs,
            "sub_frame": dataset_frame + rel_rec,
            "sub_byte_offset": word_ofs * 2,
        })

    return entries


def parse_raster_sub_dataset_header(
    adf_file: BinaryIO,
    dataset_frame: int,
    sub_frame: int,
    sub_byte_off: int,
) -> dict:
    """Read raster sub-dataset header at {sub_frame, sub_byte_off}.

    Layout (verified from NM/load2.b nm.locate.subset + load.raster.sub.dataset):
      +0: data_record_no (u16) → data_base_frame = dataset_frame + data_record_no
      +2: data_word_offset (u16) → data_byte_offset = data_word_offset × 2
      +4: gr_start_e (u16, 100m units)
      +6: gr_start_n (u16, 100m units)
      +8: gr_end_e   (u16, 100m units)
      +10: gr_end_n  (u16, 100m units)
      +12: primary_norm_factor   (s16)
      +14: secondary_norm_factor (s16)
      +16: data_size (byte: 1/2/4)
      +17: num_default_ranges (byte)
      +18+: cut points (4 bytes each, stored as pairs of u16)
    """
    byte_pos = sub_frame * BYTES_PER_FRAME + sub_byte_off
    frame_no = byte_pos // BYTES_PER_FRAME
    off      = byte_pos % BYTES_PER_FRAME

    frame = read_frame(adf_file, frame_no)

    data_record_no   = u16(frame, off)
    data_word_offset = u16(frame, off + 2)
    gr_start_e = u16(frame, off + 4)
    gr_start_n = u16(frame, off + 6)
    gr_end_e   = u16(frame, off + 8)
    gr_end_n   = u16(frame, off + 10)
    data_size  = frame[off + 16] if off + 16 < len(frame) else 1
    num_ranges = frame[off + 17] if off + 17 < len(frame) else 0
    if data_size not in (1, 2, 4):
        data_size = 1

    # Cut points: each stored as a 4-byte little-endian int32
    cut_points = []
    cp_off = off + 18
    for i in range(num_ranges):
        if cp_off + 4 > len(frame):
            break
        # 4-byte little-endian: read lo u16 then hi u16
        lo = u16(frame, cp_off)
        hi = u16(frame, cp_off + 2)
        val = (hi << 16) | lo
        # Sign-extend if needed (data could be signed)
        if val >= 0x80000000:
            val -= 0x100000000
        cut_points.append(val)
        cp_off += 4

    return {
        "data_base_frame": dataset_frame + data_record_no,
        "data_byte_offset": data_word_offset * 2,
        "gr_start_e": gr_start_e,
        "gr_start_n": gr_start_n,
        "gr_end_e": gr_end_e,
        "gr_end_n": gr_end_n,
        "data_size": data_size,
        "cut_points": cut_points,
    }


# ---------------------------------------------------------------------------
# Fine-block decoders  (from NM/unpack.b nm.get.byte.item)
# ---------------------------------------------------------------------------

def decode_fine_block_size1(fb_frame: bytes, fb_offset: int) -> list[tuple[int, int, int]]:
    """Decode size-1 (uint8 values) fine block. Returns [(loc, count, val), ...]."""
    ptr = fb_offset
    num_items = u16(fb_frame, ptr)
    if not (0 <= num_items <= 64):
        return []

    items: list[tuple[int, int, int]] = []
    next_byte = 0

    for _ in range(num_items):
        if next_byte == 0:
            ptr += 2
            if ptr + 1 >= len(fb_frame):
                break
            loc = fb_frame[ptr]
            count = fb_frame[ptr + 1]
            ptr += 2
            if ptr >= len(fb_frame):
                break
            val = fb_frame[ptr]
            next_byte = 1
        else:
            if ptr + 1 >= len(fb_frame):
                break
            loc = fb_frame[ptr + 1]
            ptr += 2
            if ptr + 1 >= len(fb_frame):
                break
            count = fb_frame[ptr]
            val = fb_frame[ptr + 1]
            next_byte = 0

        items.append((loc, count, val))

    return items


def decode_fine_block_size2(fb_frame: bytes, fb_offset: int) -> list[tuple[int, int, int]]:
    """Decode size-2 (uint16 values) fine block. Returns [(loc, count, val), ...]."""
    ptr = fb_offset
    num_items = u16(fb_frame, ptr)
    if not (0 <= num_items <= 64):
        return []
    ptr += 2  # advance past num_items word
    items: list[tuple[int, int, int]] = []
    for _ in range(num_items):
        if ptr + 3 >= len(fb_frame):
            break
        loc   = fb_frame[ptr]
        count = fb_frame[ptr + 1]
        val   = u16(fb_frame, ptr + 2)
        ptr  += 4
        items.append((loc, count, val))
    return items


def decode_fine_block(fb_frame: bytes, fb_offset: int, data_size: int) -> list[tuple[int, int, int]]:
    """Dispatch to size-1 or size-2 decoder."""
    if data_size == 2:
        return decode_fine_block_size2(fb_frame, fb_offset)
    return decode_fine_block_size1(fb_frame, fb_offset)


# ---------------------------------------------------------------------------
# BBC-inspired 5-band colour palette
# ---------------------------------------------------------------------------

MISSING_COLOR = (0, 0, 0, 0)   # transparent

# BBC Micro MODE 2 palette, matching key1.b: blue2(4), cyan2(6), green2(2), yellow2(3), red2(1)
BAND_COLORS = [
    (0,   0,   170, 255),   # blue   (colour 4) — band 1 (lowest)
    (0,   170, 170, 255),   # cyan   (colour 6) — band 2
    (0,   170, 0,   255),   # green  (colour 2) — band 3
    (170, 170, 0,   255),   # yellow (colour 3) — band 4
    (170, 0,   0,   255),   # red    (colour 1) — band 5 (highest)
]

# Fine block serpentine layout within a 32×32 km coarse block (from NM/process.b).
# 16 fine blocks (8×8 km each) ordered by (east-1)*4 + (north-1), east outer 1..4, north inner 1..4.
# East offset increases by 8 km for each group of 4 (simple column-major).
# North offset is serpentine: col 1 goes 0→24 N, col 2 goes 24→0, col 3 goes 0→24, col 4 goes 24→0.
#
# Diagram (entry numbers 1-based):
#    4   5  12  13   (N+24)
#    3   6  11  14   (N+16)
#    2   7  10  15   (N+8)
#    1   8   9  16   (N+0)
#   E+0 E+8 E+16 E+24
_FINE_E_OFFSETS = [0, 0, 0, 0, 8, 8, 8, 8, 16, 16, 16, 16, 24, 24, 24, 24]
_FINE_N_OFFSETS = [0, 8, 16, 24, 24, 16, 8, 0, 0, 8, 16, 24, 24, 16, 8, 0]


def apply_5band_colors(
    grid: np.ndarray,
    cut_points: list[int],
    data_size: int,
) -> np.ndarray:
    """Map raw values to RGBA using 5 bands.

    If cut_points is available, use them as band boundaries;
    otherwise compute equal-interval bands from min..max.

    Returns RGBA array shape (*grid.shape, 4), dtype uint8.
    """
    h, w = grid.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    valid = (grid != UNIFORM_MISSING) & (grid != 0)
    vals = grid[valid].astype(np.int32)

    if vals.size == 0:
        return rgba

    # Build 4 thresholds dividing values into 5 bands
    if len(cut_points) >= 4:
        thresholds = sorted(cut_points[:4])
    else:
        vmin, vmax = int(vals.min()), int(vals.max())
        step = max(1, (vmax - vmin) // 5)
        thresholds = [vmin + step * i for i in range(1, 5)]

    # Assign band index 0-4 using numpy (vectorised)
    band = np.zeros((h, w), dtype=np.uint8)
    v_arr = grid.astype(np.int64)
    for t in thresholds:
        band[valid & (v_arr > t)] += 1

    for b_idx, color in enumerate(BAND_COLORS):
        mask = valid & (band == b_idx)
        rgba[mask] = color

    return rgba


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_nm_region(
    adf_path: Path,
    sector_addr: int,
    e_min_km: int,
    n_min_km: int,
    e_max_km: int,
    n_max_km: int,
    sub_dataset_key: int | None = None,
) -> tuple[np.ndarray, dict]:
    """Render an NM grid-map region.

    Returns (grid_2d, metadata).
    grid_2d shape = (n_max_km - n_min_km, e_max_km - e_min_km).
    grid_2d[row, col] = raw value (row 0 = n_min_km, north-up after flip).
    Missing/zero cells hold UNIFORM_MISSING (0x8000).
    """
    height = n_max_km - n_min_km
    width  = e_max_km - e_min_km
    if height <= 0 or width <= 0:
        raise ValueError(f"Invalid bbox: E {e_min_km}–{e_max_km}, N {n_min_km}–{n_max_km}")

    grid = np.full((height, width), UNIFORM_MISSING, dtype=np.uint32)
    frame_cache: dict[int, bytes] = {}

    with open(adf_path, 'rb') as f:

        def cached_frame(frame_no: int) -> bytes:
            if frame_no not in frame_cache:
                frame_cache[frame_no] = read_frame(f, frame_no)
            return frame_cache[frame_no]

        # ── Step 1: dataset header ──────────────────────────────────────────
        hdr = parse_dataset_header(f, sector_addr)
        dataset_frame = hdr["dataset_frame"]
        index_offset  = hdr["index_offset"]

        if hdr["dataset_type"] != 1:
            raise ValueError(
                f"dataset_type={hdr['dataset_type']} is not a raster grid (type 1); "
                "only type 1 datasets can be rendered as PNG maps"
            )

        # ── Step 2: sub-dataset index ───────────────────────────────────────
        subs = parse_sub_dataset_index(f, dataset_frame, index_offset)
        if not subs:
            raise ValueError("No sub-datasets found in dataset index")

        if sub_dataset_key is None:
            sub = subs[0]
        else:
            sub = next((s for s in subs if s["key"] == sub_dataset_key), subs[0])

        # sub["key"] IS the resolution in km (from NN/display1.b choose.res).
        # Coarse block = 32 grid squares = 32 × resolution_km km.
        # Fine block   =  8 grid squares =  8 × resolution_km km.
        resolution_km = max(1, sub["key"])

        # ── Step 3: raster sub-dataset header ───────────────────────────────
        rsd = parse_raster_sub_dataset_header(
            f, dataset_frame, sub["sub_frame"], sub["sub_byte_offset"]
        )
        data_base_frame  = rsd["data_base_frame"]
        data_byte_offset = rsd["data_byte_offset"]
        gr_start_e_km    = rsd["gr_start_e"] // 10   # 100m → km
        gr_start_n_km    = rsd["gr_start_n"] // 10
        data_size        = rsd["data_size"]
        cut_points       = rsd["cut_points"]

        # ── Step 4: coarse index ─────────────────────────────────────────────
        # The coarse index is guaranteed to fit within one frame.
        coarse_frame = cached_frame(data_base_frame)
        dbo = data_byte_offset     # starting offset within coarse frame
        num_we_blocks = u16(coarse_frame, dbo)
        num_sn_blocks = u16(coarse_frame, dbo + 2)

        if num_we_blocks <= 0 or num_sn_blocks <= 0:
            raise ValueError(f"Bad coarse index dimensions: WE={num_we_blocks} SN={num_sn_blocks}")

        # Bounding-box filter: which coarse blocks overlap?
        coarse_km = 32 * resolution_km
        we_lo = max(0, (e_min_km - gr_start_e_km) // coarse_km)
        we_hi = min(num_we_blocks - 1, (e_max_km - 1 - gr_start_e_km) // coarse_km)
        sn_lo = max(0, (n_min_km - gr_start_n_km) // coarse_km)
        sn_hi = min(num_sn_blocks - 1, (n_max_km - 1 - gr_start_n_km) // coarse_km)

        # ── Step 5: iterate over relevant coarse blocks ─────────────────────
        for ci in range(num_we_blocks * num_sn_blocks):
            we = ci % num_we_blocks
            sn = ci // num_we_blocks

            if not (we_lo <= we <= we_hi and sn_lo <= sn <= sn_hi):
                continue

            off = dbo + 4 + ci * 4
            coarse_rec = get_record_number(coarse_frame, off)
            coarse_ofs = get_record_number(coarse_frame, off + 2)

            if coarse_rec == 0 and coarse_ofs == 0:
                continue
            if coarse_rec == UNIFORM_MISSING:
                continue  # uniform missing → transparent

            # Index-level compression at coarse level: entire 32×32 km block has one value.
            # BCPL: test (record.number <= 0) → uniform block.
            # Encoding: negative record.number → value = -coarse_rec; zero → value = coarse_ofs.
            if coarse_rec <= 0:
                val = (-coarse_rec) if coarse_rec < 0 else coarse_ofs
                if val > 0:
                    c_e0 = gr_start_e_km + we * coarse_km
                    c_n0 = gr_start_n_km + sn * coarse_km
                    r0 = max(0, c_n0 - n_min_km)
                    r1 = min(height, c_n0 + coarse_km - n_min_km)
                    c0 = max(0, c_e0 - e_min_km)
                    c1 = min(width, c_e0 + coarse_km - e_min_km)
                    if r0 < r1 and c0 < c1:
                        grid[r0:r1, c0:c1] = val
                continue

            fi_frame_no = data_base_frame + coarse_rec - 1
            fi_offset   = (coarse_ofs - 1) * 2

            # ── Step 6: fine index (16 entries, crosses frames if needed) ───
            # Read all 64 bytes of the fine index in one cross-frame-safe read.
            # Fine index entry k maps to east-column (k//4) and a serpentine
            # north offset — see _FINE_E_OFFSETS / _FINE_N_OFFSETS (from process.b).
            fi_data = read_cross_frame(cached_frame, fi_frame_no, fi_offset, 64)

            for k in range(16):
                off_k = k * 4
                fine_rec = get_record_number(fi_data, off_k)
                fine_ofs = get_record_number(fi_data, off_k + 2)

                if fine_rec == 0 and fine_ofs == 0:
                    continue
                if fine_rec == UNIFORM_MISSING:
                    continue  # uniform missing → transparent

                # Index-level compression at fine level: entire 8×8 km block has one value.
                # Encoding: negative fine_rec → value = -fine_rec; zero → value = fine_ofs.
                fine_km = 8 * resolution_km
                if fine_rec <= 0:
                    val = (-fine_rec) if fine_rec < 0 else fine_ofs
                    if val > 0:
                        f_e0 = gr_start_e_km + we * coarse_km + _FINE_E_OFFSETS[k] * resolution_km
                        f_n0 = gr_start_n_km + sn * coarse_km + _FINE_N_OFFSETS[k] * resolution_km
                        r0 = max(0, f_n0 - n_min_km)
                        r1 = min(height, f_n0 + fine_km - n_min_km)
                        c0 = max(0, f_e0 - e_min_km)
                        c1 = min(width, f_e0 + fine_km - e_min_km)
                        if r0 < r1 and c0 < c1:
                            grid[r0:r1, c0:c1] = val
                    continue

                fb_frame_no = data_base_frame + fine_rec - 1
                fb_offset   = (fine_ofs - 1) * 2

                # Read fine block data cross-frame-safe (max ~194 bytes; use 256 for safety).
                fb_data = read_cross_frame(cached_frame, fb_frame_no, fb_offset, 256)
                items = decode_fine_block(fb_data, 0, data_size)

                # Base OS grid coordinates of this fine block in km.
                # Serpentine layout: east column = k//4, north from lookup table.
                base_e = gr_start_e_km + we * coarse_km + _FINE_E_OFFSETS[k] * resolution_km
                base_n = gr_start_n_km + sn * coarse_km + _FINE_N_OFFSETS[k] * resolution_km

                for loc, count, val in items:
                    if loc < 1:
                        continue
                    for c in range(count):
                        loc2 = loc + c
                        if loc2 > 64:
                            break
                        n_idx = (loc2 - 1) // 8   # 0-7 S→N within fine block
                        e_idx = (loc2 - 1) % 8    # 0-7 W→E within fine block
                        e_km  = base_e + e_idx * resolution_km
                        n_km  = base_n + n_idx * resolution_km
                        r0 = max(0, n_km - n_min_km)
                        r1 = min(height, n_km - n_min_km + resolution_km)
                        c0 = max(0, e_km - e_min_km)
                        c1 = min(width, e_km - e_min_km + resolution_km)
                        if r0 < r1 and c0 < c1:
                            grid[r0:r1, c0:c1] = val

    metadata = {
        "dataset_type": hdr["dataset_type"],
        "cut_points": cut_points,
        "data_size": data_size,
        "gr_start_e": rsd["gr_start_e"],
        "gr_start_n": rsd["gr_start_n"],
        "gr_end_e": rsd["gr_end_e"],
        "gr_end_n": rsd["gr_end_n"],
        "num_we_blocks": num_we_blocks,
        "num_sn_blocks": num_sn_blocks,
        "resolution_km": resolution_km,
    }
    return grid, metadata


# ---------------------------------------------------------------------------
# Text metadata helpers
# ---------------------------------------------------------------------------

def read_nm_text_addresses(adf_path: Path, sector_addr: int) -> dict:
    """Read the 3 text addresses from the NM dataset header.

    Layout (NM/load1.b bytes 2–13 relative to dataset start in ADF frame):
      +2..+5:   private_text_address     (32-bit LE uint32)
      +6..+9:   descriptive_text_address (32-bit LE uint32)
      +10..+13: technical_text_address   (32-bit LE uint32)

    Returns dict with 'private', 'descriptive', 'technical' as 32-bit item
    addresses (same format as NAMES addresses; decode with decode_names_address
    from parser.py).
    """
    frame_no, frame_off = _sector_to_frame_and_offset(sector_addr)
    with open(adf_path, 'rb') as f:
        frame = read_frame(f, frame_no)
    return {
        'private':     struct.unpack_from('<I', frame, frame_off + 2)[0],
        'descriptive': struct.unpack_from('<I', frame, frame_off + 6)[0],
        'technical':   struct.unpack_from('<I', frame, frame_off + 10)[0],
    }


def parse_nm_classification(text_pages: list[str]) -> dict[int, str]:
    """Extract integer→label mapping from NM descriptive text pages.

    Matches lines like '    42 OOLITE UNCLASSED' or '  0 water'.
    """
    result: dict[int, str] = {}
    pattern = re.compile(r'^\s{0,8}(\d+)\s{1,4}([A-Za-z].{2,})')
    for page in text_pages:
        for line in page.splitlines():
            m = pattern.match(line)
            if m:
                n, label = int(m.group(1)), m.group(2).strip()
                result[n] = label
    return result


# ---------------------------------------------------------------------------
# Distinct-colour rendering helpers
# ---------------------------------------------------------------------------

# Standard Viridis colour stops (key points of the matplotlib viridis colormap)
_VIRIDIS_STOPS: list[tuple[int, int, int]] = [
    (68,  1,  84), (72, 40, 120), (62, 83, 160), (49, 104, 142),
    (38, 130, 142), (31, 158, 137), (53, 183, 121), (109, 205,  89),
    (180, 222,  44), (253, 231,  37),
]


def _viridis_colors(n: int) -> list[tuple[int, int, int, int]]:
    """Sample n evenly-spaced colours from the Viridis ramp via linear interpolation."""
    if n <= 0:
        return []
    if n == 1:
        # Return the midpoint colour
        mid = len(_VIRIDIS_STOPS) / 2
        lo = int(mid)
        hi = min(lo + 1, len(_VIRIDIS_STOPS) - 1)
        t = mid - lo
        r = int(_VIRIDIS_STOPS[lo][0] + t * (_VIRIDIS_STOPS[hi][0] - _VIRIDIS_STOPS[lo][0]))
        g = int(_VIRIDIS_STOPS[lo][1] + t * (_VIRIDIS_STOPS[hi][1] - _VIRIDIS_STOPS[lo][1]))
        b = int(_VIRIDIS_STOPS[lo][2] + t * (_VIRIDIS_STOPS[hi][2] - _VIRIDIS_STOPS[lo][2]))
        return [(r, g, b, 255)]
    stops = _VIRIDIS_STOPS
    result = []
    for i in range(n):
        pos = i / (n - 1) * (len(stops) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(stops) - 1)
        t = pos - lo
        r = int(stops[lo][0] + t * (stops[hi][0] - stops[lo][0]))
        g = int(stops[lo][1] + t * (stops[hi][1] - stops[lo][1]))
        b = int(stops[lo][2] + t * (stops[hi][2] - stops[lo][2]))
        result.append((r, g, b, 255))
    return result


def _greyscale_colors(n: int) -> list[tuple[int, int, int, int]]:
    """Return n evenly-spaced grey levels from dark (20,20,20) to bright (240,240,240)."""
    if n <= 0:
        return []
    if n == 1:
        return [(130, 130, 130, 255)]
    return [
        (int(20 + 220 * i / (n - 1)),) * 3 + (255,)
        for i in range(n)
    ]


def _golden_ratio_colors(n: int) -> list[tuple[int, int, int, int]]:
    """Generate n perceptually distinct RGBA colors using golden-ratio hue spacing."""
    golden = 0.618033988749895
    colors = []
    for i in range(n):
        h = (i * golden) % 1.0
        s = 0.75 + 0.15 * ((i // 8) % 2)
        v = 0.92 - 0.12 * ((i // 16) % 2)
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        colors.append((int(r * 255), int(g * 255), int(b * 255), 255))
    return colors


def apply_distinct_colors(
    grid: np.ndarray,
    palette: str = "golden",
) -> tuple[np.ndarray, dict[int, tuple[int, int, int, int]]]:
    """Assign a unique RGBA colour to every distinct raw value in the grid.

    palette: "golden" (default), "viridis", or "greyscale"
    Returns (rgba_array, val_to_color).
    """
    h, w = grid.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = (grid != UNIFORM_MISSING) & (grid != 0)
    unique_vals = sorted(int(v) for v in np.unique(grid[valid]))
    if not unique_vals:
        return rgba, {}
    n = len(unique_vals)
    if palette == "viridis":
        colors = _viridis_colors(n)
    elif palette == "greyscale":
        colors = _greyscale_colors(n)
    else:
        colors = _golden_ratio_colors(n)
    val_to_color: dict[int, tuple[int, int, int, int]] = {}
    for i, val in enumerate(unique_vals):
        color = colors[i]
        val_to_color[val] = color
        rgba[grid == val] = color
    return rgba, val_to_color


# ---------------------------------------------------------------------------
# Legend builder
# ---------------------------------------------------------------------------

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def _make_legend(
    entries: list[tuple[str, tuple[int, int, int, int]]],
    target_height: int,
) -> Image.Image:
    """Build a legend strip: colored swatches with text labels."""
    n = len(entries)
    if n == 0:
        return Image.new("RGBA", (10, max(1, target_height)), (30, 30, 30, 255))

    row_h = max(14, min(32, target_height // max(1, n)))
    swatch = row_h - 4
    pad = 6
    font = _load_font(max(9, row_h - 6))

    # Measure maximum label width
    dummy = Image.new("RGBA", (1, 1))
    dd = ImageDraw.Draw(dummy)
    max_tw = max(
        (dd.textbbox((0, 0), label, font=font)[2] for label, _ in entries),
        default=40,
    )
    legend_w = pad + swatch + pad + max_tw + pad
    legend_h = max(target_height, n * row_h + 2 * pad)

    img = Image.new("RGBA", (legend_w, legend_h), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img)
    for i, (label, color) in enumerate(entries):
        y = pad + i * row_h
        draw.rectangle([pad, y, pad + swatch, y + swatch], fill=color)
        draw.text((pad + swatch + pad, y), label, fill=(220, 220, 220, 255), font=font)
    return img


def _make_gradient_legend(
    colors: list[tuple[int, int, int, int]],
    min_val: int,
    max_val: int,
    target_height: int,
) -> Image.Image:
    """Build a vertical gradient legend strip for ramp colour modes.

    Draws from top (max_val, brightest) to bottom (min_val, darkest).
    """
    bar_w = 20
    pad = 6
    font = _load_font(10)
    dummy = Image.new("RGBA", (1, 1))
    dd = ImageDraw.Draw(dummy)
    max_label = str(max_val)
    min_label = str(min_val)
    max_tw = max(
        dd.textbbox((0, 0), max_label, font=font)[2],
        dd.textbbox((0, 0), min_label, font=font)[2],
    )
    legend_w = pad + bar_w + pad + max_tw + pad
    legend_h = max(target_height, 60)

    img = Image.new("RGBA", (legend_w, legend_h), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img)

    bar_top = pad + 14  # leave room for top label
    bar_bot = legend_h - pad - 14  # leave room for bottom label
    bar_h = max(1, bar_bot - bar_top)

    n = len(colors)
    for y in range(bar_h):
        # y=0 → top → max (high index in colors); y=bar_h-1 → bottom → min (index 0)
        frac = 1.0 - y / max(1, bar_h - 1)
        pos = frac * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        t = pos - lo
        c_lo = colors[lo]
        c_hi = colors[hi]
        r = int(c_lo[0] + t * (c_hi[0] - c_lo[0]))
        g = int(c_lo[1] + t * (c_hi[1] - c_lo[1]))
        b = int(c_lo[2] + t * (c_hi[2] - c_lo[2]))
        draw.line([(pad, bar_top + y), (pad + bar_w, bar_top + y)], fill=(r, g, b, 255))

    # Labels
    draw.text((pad, pad), max_label, fill=(220, 220, 220, 255), font=font)
    draw.text((pad, bar_bot + 2), min_label, fill=(220, 220, 220, 255), font=font)

    return img


# ---------------------------------------------------------------------------
# Title header builder
# ---------------------------------------------------------------------------

def _make_header(title: str, path: list[str], width: int) -> Image.Image:
    """Build a BBC-style title bar above the map.

    Top row: dataset title in bright yellow.
    Bottom row: hierarchy breadcrumb in cyan.
    """
    pad = 6
    title_font = _load_font(14)
    path_font  = _load_font(11)

    dummy = Image.new("RGBA", (1, 1))
    dd = ImageDraw.Draw(dummy)
    title_h = dd.textbbox((0, 0), title or "Untitled", font=title_font)[3] + 2
    path_h  = dd.textbbox((0, 0), "X", font=path_font)[3] + 2

    header_h = pad + title_h + pad + path_h + pad
    img = Image.new("RGBA", (width, header_h), (10, 10, 40, 255))
    draw = ImageDraw.Draw(img)

    # Separator line at bottom
    draw.line([(0, header_h - 1), (width - 1, header_h - 1)], fill=(0, 170, 170, 200))

    # Title row
    draw.text((pad, pad), title or "Untitled", fill=(255, 255, 85, 255), font=title_font)

    # Breadcrumb path row
    breadcrumb = "  \u25b8  ".join(p.title() for p in path) if path else ""
    draw.text((pad, pad + title_h + pad), breadcrumb, fill=(85, 255, 255, 200), font=path_font)

    return img


# ---------------------------------------------------------------------------
# PNG rendering
# ---------------------------------------------------------------------------

def grid_to_png(
    grid: np.ndarray,
    meta: dict,
    *,
    mode: str = "bands",
    scale: int = 1,
    legend: bool = False,
    title: str = "",
    path: list[str] | None = None,
    classification: dict[int, str] | None = None,
) -> bytes:
    """Convert a rendered NM grid to a PNG byte string.

    mode:           "bands"    — 5-band BBC-style choropleth (default)
                    "distinct" — one unique colour per raw data value
    scale:          pixel multiplier 1–8 (1 km² = scale×scale pixels)
    legend:         if True, append a colour-key strip on the right
    title:          dataset name shown in the header bar
    path:           hierarchy breadcrumb list shown below the title
    classification: optional int→label dict (from parse_nm_classification);
                    used as legend labels when mode="distinct" and legend=True
    """
    scale = max(1, min(8, scale))

    if mode in ("distinct", "viridis", "greyscale"):
        palette = mode if mode in ("viridis", "greyscale") else "golden"
        rgba, val_to_color = apply_distinct_colors(grid, palette=palette)
    else:
        rgba = apply_5band_colors(grid, meta.get("cut_points", []), meta.get("data_size", 1))
        val_to_color = None

    # North at top: row 0 in grid = south (n_min); flip so top of image = north.
    img = Image.fromarray(rgba[::-1, :, :], mode="RGBA")

    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)

    if legend:
        if mode in ("viridis", "greyscale") and val_to_color:
            sorted_vals = sorted(val_to_color.keys())
            min_val = sorted_vals[0]
            max_val = sorted_vals[-1]
            n = len(sorted_vals)
            colors_list = _viridis_colors(n) if mode == "viridis" else _greyscale_colors(n)
            leg = _make_gradient_legend(colors_list, min_val, max_val, img.height)
        elif mode == "distinct" and val_to_color:
            entries: list[tuple[str, tuple]] = [
                (classification.get(v, str(v)) if classification else str(v), c)
                for v, c in sorted(val_to_color.items())
            ]
            leg = _make_legend(entries, img.height)
        else:
            cut_pts = sorted(meta.get("cut_points", []))
            entries = []
            for i, color in enumerate(BAND_COLORS):
                if i < len(cut_pts):
                    label = f"\u2264{cut_pts[i]}"
                elif cut_pts:
                    label = f">{cut_pts[-1]}"
                else:
                    label = f"Band {i + 1}"
                entries.append((label, color))
            leg = _make_legend(entries, img.height)

        combined = Image.new("RGBA", (img.width + 8 + leg.width, max(img.height, leg.height)),
                             (30, 30, 30, 255))
        combined.paste(img, (0, 0))
        combined.paste(leg, (img.width + 8, 0))
        img = combined

    # Title / hierarchy header bar
    if title or path:
        hdr = _make_header(title, path or [], img.width)
        final = Image.new("RGBA", (img.width, hdr.height + img.height), (10, 10, 40, 255))
        final.paste(hdr, (0, 0))
        final.paste(img, (0, hdr.height))
        img = final

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

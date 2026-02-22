"""Binary parser for BBC Domesday Walk/Gallery NW datasets.

Binary format verified against BCPL source (build/src/NW/walk1.b, walk2.b, H/nwhd.h)
and hex dump of the GALLERY file.

BCPL notation:
  r(d)  = g.ut.unpack16.signed(g.nw, d+d)  → signed int16 at byte offset 2*d
  ru(d) = g.ut.unpack16(g.nw, d+d)          → unsigned int16 at byte offset 2*d

Header layout (each r(n) reads a signed int16 at byte offset 2*n):
  r(14) @ byte 28 → ltable byte-offset  (divide by 2 for word index)
  r(16) @ byte 32 → ctable byte-offset
  r(18) @ byte 36 → ptable byte-offset
  r(20) @ byte 40 → dtable byte-offset
  r(25) @ byte 50 → detail-table word-count
  r(27) @ byte 54 → base_view + 1        (read as UNSIGNED — can exceed 32767)
  r(28) @ byte 56 → base_plan            (read as UNSIGNED)
  r(29) @ byte 58 → syslev (1 = gallery)

Total data bytes in the dataset: r(20) + r(25)*2
"""

from __future__ import annotations

import struct
from pathlib import Path

from domesday.models import DetailIcon, LinkTarget, Node, PlanPosition, WalkDataset


# ---------------------------------------------------------------------------
# Public helpers (also tested directly)
# ---------------------------------------------------------------------------


def rightof(view: int) -> int:
    """Return the view 45° to the right.

    Views are 1-based BCPL absolute view numbers.
    Matches BCPL exactly: rightof.(view) = (view & 7) = 0 → view-7, view+1

    Examples: rightof(7) = 8, rightof(8) = 1, rightof(16) = 9
    """
    return view - 7 if (view & 7) == 0 else view + 1


def leftof(view: int) -> int:
    """Return the view 45° to the left (1-based BCPL view numbers).

    Matches BCPL: leftof.(view) = (view & 7) = 1 → view+7, view-1

    Examples: leftof(1) = 8, leftof(8) = 7, leftof(9) = 16
    """
    return view + 7 if (view & 7) == 1 else view - 1


def unpack_plan_position(data: bytes, byte_offset: int, view: int) -> PlanPosition:
    """Unpack a two-word plan table entry.

    Args:
        data: raw bytes of the plan table entry (4 bytes: y_word then x_word)
        byte_offset: byte offset of the entry within `data`
        view: the 1-based view number (used to compute compass direction)

    Returns:
        PlanPosition with x, y, plan_number and direction fields populated.

    From walk2.b:
        let x, y = ru(ptable+pos+1), ru(ptable+pos)
        plan      := y >> 12
        direction := (8 - (x >> 12) + view) rem 8
    """
    y_word, x_word = struct.unpack_from("<HH", data, byte_offset)
    plan_y = y_word & 0x0FFF
    plan_number = y_word >> 12
    plan_x = x_word & 0x0FFF
    base_dir = x_word >> 12
    return PlanPosition(x=plan_x, y=plan_y, plan_number=plan_number, base_direction=base_dir)


def parse_gallery_item(data: bytes, dtable_byte: int, item_offset: int) -> int:
    """Gallery mode: read the 32-bit NAMES record index stored at dtable_byte + item_offset*2."""
    return struct.unpack_from('<I', data, dtable_byte + item_offset * 2)[0]


# Item types from BCPL sthd.h
NAMES_TYPE_LABELS = {
    1: 'Map', 2: 'Map', 3: 'Map',
    4: 'Chart',
    6: 'Text', 7: 'Text',
    8: 'Photo',
    9: 'Walk',
    10: 'Film',
}


def parse_names_record(names_data: bytes, record_index: int) -> dict:
    """Read one 36-byte NAMES record.

    Layout:
      bytes  0-30: title string (strip trailing spaces/nulls)
      byte  31:    item type (0-10)
      bytes 32-35: 32-bit LaserDisc frame address (little-endian uint32)
    """
    pos = record_index * 36
    title = names_data[pos:pos + 31].decode('latin-1').rstrip('\x00 ')
    while title and ord(title[0]) < 0x20:
        title = title[1:]
    item_type = names_data[pos + 31]
    address = struct.unpack_from('<I', names_data, pos + 32)[0]
    return {'title': title, 'type': item_type, 'address': address}


def decode_names_address(address32: int) -> tuple[bool, int]:
    """Decode a 32-bit NAMES address into (is_data2, file_byte_offset).

    Bit 15 of the high word is the DATA2 flag; remaining 31 bits are the byte offset.
    """
    low = address32 & 0xFFFF
    high = (address32 >> 16) & 0xFFFF
    is_data2 = bool(high & 0x8000)
    return is_data2, (high & 0x7FFF) * 65536 + low


def parse_photo_frames(data_path: Path, file_offset: int) -> list[int]:
    """Read frame numbers from a photo set stored in DATA1 or DATA2.

    Photo record layout at file_offset:
      bytes  0-27: header / padding
      bytes 28-29: num_pics_raw (uint16 LE); actual count = value & 0x7FFF
      bytes 30 + i*2: frame_i (uint16 LE) for i in 0..num_pics-1
    """
    with data_path.open('rb') as f:
        f.seek(file_offset + 28)
        num_pics_raw = struct.unpack('<H', f.read(2))[0]
        num_pics = num_pics_raw & 0x7FFF
        frame_bytes = f.read(num_pics * 2)
    return [struct.unpack_from('<H', frame_bytes, i * 2)[0] for i in range(num_pics)]


def parse_photo_set(data_path: Path, file_offset: int) -> dict:
    """Read frames, short captions, and long descriptions from a photo set in DATA1/DATA2.

    Photo record layout at file_offset:
      bytes  0-27: header / padding
      bytes 28-29: num_pics_raw (uint16 LE)
                   bit 15 = 0 → 4-line long captions (m.np.small.lc × 39 = 156 bytes each)
                   bit 15 = 1 → 8-line long captions (m.np.large.lc × 39 = 312 bytes each)
                   bits 0-14  → num_pics
      bytes 30 + i*2:                    frame[i] (uint16 LE), i = 0..num_pics-1
      bytes 30 + num_pics*2 + i*30:      short_caption[i] (30 bytes), i = 0..num_pics-1
      bytes above + num_pics*30 + i*lcs: long_caption[i] (lcs bytes), i = 0..num_pics-1

    Returns dict with keys 'frames', 'captions', 'descriptions' (all lists, one entry per photo).
    """
    SC_LEN = 30   # m.np.sclength
    LC_LEN = 39   # m.np.lclength (chars per line)

    with data_path.open('rb') as f:
        f.seek(file_offset + 28)
        num_pics_raw = struct.unpack('<H', f.read(2))[0]
        num_pics = num_pics_raw & 0x7FFF
        descr_lines = 8 if (num_pics_raw & 0x8000) else 4
        lc_size = descr_lines * LC_LEN  # bytes per long caption entry

        # Frames (file pointer is now at file_offset + 30)
        frame_bytes = f.read(num_pics * 2)
        frames = [struct.unpack_from('<H', frame_bytes, i * 2)[0] for i in range(num_pics)]

        # Short captions (immediately follow frame list)
        sc_bytes = f.read(num_pics * SC_LEN)
        captions = []
        for i in range(num_pics):
            raw = sc_bytes[i * SC_LEN:(i + 1) * SC_LEN]
            cap = raw.decode('latin-1').rstrip('\x00 ')
            while cap and ord(cap[0]) < 0x20:
                cap = cap[1:]
            captions.append(cap)

        # Long captions (immediately follow short captions)
        lc_bytes = f.read(num_pics * lc_size)
        descriptions = []
        for i in range(num_pics):
            lines = []
            for ln in range(descr_lines):
                start = i * lc_size + ln * LC_LEN
                line = lc_bytes[start:start + LC_LEN].decode('latin-1').rstrip('\x00 ')
                if line:
                    lines.append(line)
            descriptions.append('\n'.join(lines))

    return {'frames': frames, 'captions': captions, 'descriptions': descriptions}


def parse_closeup_frames(data: bytes, dtable_byte: int, item_offset: int, base_view: int) -> list[int]:
    """Return absolute LaserDisc frame numbers for a detail closeup chain.

    Layout at dtable_byte + item_offset*2:
      word 0 (uint16): count N
      words 1..N (int16): frame offsets relative to base_view
    """
    pos = dtable_byte + item_offset * 2
    count = _read_u16(data, pos)
    return [
        base_view + _read_i16(data, pos + cu * 2)
        for cu in range(1, count + 1)
    ]


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------


def _read_i16(data: bytes, byte_offset: int) -> int:
    """Signed little-endian int16."""
    return struct.unpack_from("<h", data, byte_offset)[0]


def _read_u16(data: bytes, byte_offset: int) -> int:
    """Unsigned little-endian uint16."""
    return struct.unpack_from("<H", data, byte_offset)[0]


def _parse_details(data: bytes, dtable_byte: int, detail_word_offset: int) -> list[DetailIcon]:
    """Parse the variable-length detail icon list for one view.

    Each entry is 3 × int16: [x_raw, y, item_offset].
    The list terminates when x_raw < 0 (x = abs(x_raw)).
    """
    icons: list[DetailIcon] = []
    pos = dtable_byte + detail_word_offset * 2
    while True:
        x_raw = _read_i16(data, pos)
        y = _read_i16(data, pos + 2)
        item_offset = _read_i16(data, pos + 4)
        icons.append(DetailIcon(x=abs(x_raw), y=y, item_offset=item_offset))
        if x_raw < 0:
            break
        pos += 6  # advance by 3 words
    return icons


def _parse_link(data: bytes, ltable_word: int, k: int) -> tuple[int, bool, int]:
    """Read a cross-dataset link entry from the link table.

    For ctable next_view = -k (k positive):
      32-bit dataset byte-offset:  words at ltable_word+k and ltable_word+k+1
      next view number:            word at ltable_word+k+2

    Returns:
        (dataset_byte_offset, use_data2, linked_view)
    """
    byte_pos = (ltable_word + k) * 2
    low = _read_u16(data, byte_pos)
    high = _read_u16(data, byte_pos + 2)
    linked_view = _read_u16(data, byte_pos + 4)
    dataset_offset = ((high & 0x7FFF) << 16) | low
    use_data2 = bool(high & 0x8000)
    return dataset_offset, use_data2, linked_view


# ---------------------------------------------------------------------------
# Core dataset parser
# ---------------------------------------------------------------------------


def parse_dataset(data: bytes, source_file: Path | None = None, byte_offset: int = 0) -> WalkDataset:
    """Parse a raw NW dataset from `data` starting at `byte_offset`.

    Works for both the main GALLERY dataset (byte_offset=0) and any
    sub-dataset embedded at a non-zero byte offset within the same file.
    """
    base = byte_offset  # alias for readability

    # --- Header -----------------------------------------------------------
    # All table offsets from the header are BYTE offsets into the dataset.
    # We convert to word offsets (divide by 2) for consistency with BCPL.
    ltable_byte = _read_i16(data, base + 28)  # r(14)
    ctable_byte = _read_i16(data, base + 32)  # r(16)
    ptable_byte = _read_i16(data, base + 36)  # r(18)
    dtable_byte = _read_i16(data, base + 40)  # r(20)

    ltable_word = ltable_byte // 2
    ctable_word = ctable_byte // 2
    ptable_word = ptable_byte // 2
    dtable_word = dtable_byte // 2

    # base_view and base_plan may exceed 32767 on non-gallery datasets,
    # so read them as UNSIGNED int16.
    base_view = _read_u16(data, base + 54) - 1  # r(27) - 1
    base_plan = _read_u16(data, base + 56)  # r(28)
    syslev = _read_i16(data, base + 58)  # r(29)

    # --- Number of views in ctable ---------------------------------------
    # ctable runs from ctable_byte to ptable_byte; each entry is 4 bytes
    # (2 × int16: next_view, detail_offset).
    # Entry 0 stores the initial view number, not a real node.
    n_ctable_entries = (ptable_byte - ctable_byte) // 4
    initial_view = _read_i16(data, base + ctable_byte)  # r(ctable_word)

    # --- Build nodes -------------------------------------------------------
    nodes: dict[int, Node] = {}

    for v in range(1, n_ctable_entries):
        entry_byte = base + ctable_byte + v * 4
        next_view_raw = _read_i16(data, entry_byte)  # r(ctable_word + 2*v)
        detail_offset = _read_i16(data, entry_byte + 2)  # r(ctable_word + 2*v + 1)

        frame = base_view + v

        # Resolve forward navigation
        if next_view_raw == 0:
            # Dead-end: nothing ahead
            forward = None
            linked = False
            link_target = None
        elif next_view_raw < 0:
            # Cross-dataset link: k = -next_view_raw
            k = -next_view_raw
            dataset_offset, use_data2, linked_view = _parse_link(data, base // 2 + ltable_word, k)
            forward = linked_view
            linked = True
            link_target = LinkTarget(byte_offset=dataset_offset, use_data2=use_data2)
        else:
            forward = next_view_raw
            linked = False
            link_target = None

        # Resolve plan position (groups of 8 views share one position entry)
        position_idx = (v - 1) // 8  # 0-based position group index
        position_word = ptable_word + position_idx * 2
        pt_byte = base + position_word * 2
        if pt_byte + 4 <= len(data):
            plan = unpack_plan_position(data, pt_byte, view=v)
        else:
            plan = None

        # Resolve detail icons
        if detail_offset >= 0:
            details = _parse_details(data, base + dtable_byte, detail_offset)
        else:
            details = []

        nodes[v] = Node(
            view=v,
            frame=frame,
            forward=forward,
            linked_dataset=linked,
            link_target=link_target,
            details=details,
            plan=plan,
        )

    return WalkDataset(
        source_file=source_file or Path("<memory>"),
        syslev=syslev,
        base_view=base_view,
        base_plan=base_plan,
        initial_view=initial_view,
        nodes=nodes,
        dtable_byte=dtable_byte,
    )


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_gallery(path: Path) -> WalkDataset:
    """Load and parse the main NW dataset from a GALLERY file (byte offset 0)."""
    data = path.read_bytes()
    return parse_dataset(data, source_file=path, byte_offset=0)


def load_walk(data_file: Path, byte_offset: int) -> WalkDataset:
    """Load a walk dataset at `byte_offset` bytes into a DATA1 or DATA2 file."""
    data = data_file.read_bytes()
    return parse_dataset(data, source_file=data_file, byte_offset=byte_offset)


def load_gallery_subdataset(gallery_path: Path, byte_offset: int) -> WalkDataset:
    """Load a sub-dataset embedded in the GALLERY file at `byte_offset`."""
    data = gallery_path.read_bytes()
    return parse_dataset(data, source_file=gallery_path, byte_offset=byte_offset)

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

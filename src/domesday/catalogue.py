"""
domesday.catalogue — Extract the full dataset catalogue from the National disc.

Reads HIERARCHY (thesaurus tree) and NAMES (item index) and outputs every
dataset reachable via the National Contents menu, together with its navigation
path and item type.

CLI usage
---------
    python -m domesday.catalogue [--data DIR] [--format text|csv|json] [--type TYPE]

Programmatic usage
------------------
    from domesday.catalogue import extract_catalogue
    entries = extract_catalogue(Path("data/NationalA"))
    for e in entries:
        print(" > ".join(e.path), "–", e.name, f"({e.type_name})")
"""

import csv
import io
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

# ── On-disc record sizes ──────────────────────────────────────────────────────

THES_REC_SIZE  = 128   # bytes per HIERARCHY record  (270336 / 128 = 2112)
NAMES_REC_SIZE = 36    # bytes per NAMES record       (444312 /  36 = 12342)
NULL32         = 0xFFFFFFFF   # null / terminator value in 32-bit pointer fields

# ── HIERARCHY record byte offsets (nthd.h) ────────────────────────────────────
#
#  Byte  Size  Field
#   0     4    father      – byte offset of parent record (NULL32 = root)
#   4     2    pic         – unused
#   6     4    text        – 32-bit address of level-2 node essay (NULL32 = none)
#  10    32    title       – BCPL string: length byte + up to 30 chars + pad
#  42     1    bottomflag  – 128 = leaf node (HDPs → NAMES), 0 = non-leaf
#  43     1    level       – depth in hierarchy (0 = root)
#  44    80    HDPs        – 20 × uint32 child pointers; NULL32 terminates list
#                            non-leaf: byte offsets into HIERARCHY
#                            leaf:     record numbers into NAMES
# 124     4    xref        – byte offset of cross-reference list (NULL32/0 = none)

NT_FATHER      = 0
NT_TEXT        = 6
NT_TITLE       = 10
NT_BOTTOMFLAG  = 42
NT_LEVEL       = 43
NT_HDPS        = 44
NT_XREF        = 124
NT_NUM_HDPS    = 20

# ── NAMES record byte offsets ─────────────────────────────────────────────────
#
#  Byte  Size  Field
#   0    31    name    – BCPL string: length byte + up to 30 chars
#  31     1    type    – item type (see ITEM_TYPE_NAMES)
#  32     4    address – 32-bit disc address (little-endian)
#                        for NM datasets: absolute sector number
#                        bit 15 of high word → DATA2 file, else same/DATA1

NAMES_NAME = 0
NAMES_TYPE = 31
NAMES_ADDR = 32

ITEM_TYPE_NAMES = {
    1: "Grid map",
    2: "Areal boundary",
    3: "Areal map",
    4: "Data/Chart",
    5: "Plan",
    6: "Essay",
    7: "Essay (AA text)",
    8: "Photo",
    9: "Walk",
    10: "Film",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ThesRecord:
    """One parsed record from the HIERARCHY file."""
    offset:      int        # byte offset of this record in HIERARCHY
    father:      int        # byte offset of parent (NULL32 = root / no parent)
    text_addr:   int        # 32-bit address of optional level-2 node essay
    title:       str        # decoded title string
    is_leaf:     bool       # True when bottomflag == 128 (HDPs point to NAMES)
    level:       int        # depth: 0 = root "British Life in the 1980s"
    hdps:        list       # child byte-offsets (non-leaf) or NAMES record nos (leaf)
    xref_offset: int        # byte offset of xref list (0 or NULL32 = none)


@dataclass
class DatasetEntry:
    """One dataset item discovered in the catalogue."""
    path:      list    # navigation path, e.g. ["People", "Households", "Composition"]
    name:      str     # item name from NAMES file
    item_type: int     # numeric type code
    type_name: str     # human-readable type string
    address:   int     # 32-bit disc address
    record_no: int     # 0-based index into the NAMES file

    def path_str(self, sep: str = " > ") -> str:
        return sep.join(self.path) if self.path else "(root)"


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _u32(data: bytes, offset: int) -> int:
    """Read a little-endian uint32."""
    return struct.unpack_from('<I', data, offset)[0]


def _bcpl_str(data: bytes, offset: int, max_len: int = 30) -> str:
    """Read a BCPL string: length byte at `offset`, then `length` chars."""
    length = data[offset]
    raw = data[offset + 1: offset + 1 + min(length, max_len)]
    return raw.decode('latin-1', errors='replace').rstrip()


# ── Record parsers ────────────────────────────────────────────────────────────

def _parse_thes(hier: bytes, byte_offset: int) -> ThesRecord:
    b = hier[byte_offset: byte_offset + THES_REC_SIZE]
    father      = _u32(b, NT_FATHER)
    text_addr   = _u32(b, NT_TEXT)
    title       = _bcpl_str(b, NT_TITLE)
    is_leaf     = b[NT_BOTTOMFLAG] == 128
    level       = b[NT_LEVEL]
    xref_offset = _u32(b, NT_XREF)

    hdps = []
    for i in range(NT_NUM_HDPS):
        val = _u32(b, NT_HDPS + i * 4)
        if val == NULL32:
            break
        hdps.append(val)

    return ThesRecord(byte_offset, father, text_addr, title,
                      is_leaf, level, hdps, xref_offset)


def _parse_names(names: bytes, record_no: int) -> tuple:
    """Return (name, item_type, address) for a NAMES record."""
    off  = record_no * NAMES_REC_SIZE
    b    = names[off: off + NAMES_REC_SIZE]
    name = _bcpl_str(b, NAMES_NAME)
    return name, b[NAMES_TYPE], _u32(b, NAMES_ADDR)


# ── Path building ─────────────────────────────────────────────────────────────

def _build_path(hier: bytes, start_offset: int) -> list:
    """
    Walk father-pointer chain from start_offset upward.
    Returns the navigation path (list of title strings, level-1 first).
    Level-0 root ("British Life in the 1980s") is omitted from the path.
    """
    path = []
    cur  = start_offset
    seen = set()
    while cur != NULL32 and 0 <= cur + THES_REC_SIZE <= len(hier):
        if cur in seen:
            break
        seen.add(cur)
        rec = _parse_thes(hier, cur)
        if rec.level == 0:
            break                   # reached root — stop, don't include it
        path.append(rec.title)
        cur = rec.father
    path.reverse()
    return path


# ── Main extraction ───────────────────────────────────────────────────────────

def extract_catalogue(data_dir: Path) -> list:
    """
    Parse HIERARCHY and NAMES files under `data_dir/VFS/` and return a list
    of DatasetEntry objects covering every dataset in the thesaurus.

    Each entry carries:
      path      – list of thesaurus titles from level-1 down to the leaf node
      name      – dataset name from the NAMES file
      item_type – numeric type code
      type_name – human-readable type string
      address   – 32-bit disc address
      record_no – 0-based NAMES file record number
    """
    hier  = (data_dir / 'VFS' / 'HIERARCHY').read_bytes()
    names = (data_dir / 'VFS' / 'NAMES').read_bytes()

    num_thes  = len(hier)  // THES_REC_SIZE     # 2112
    num_names = len(names) // NAMES_REC_SIZE    # 12342

    entries      = []
    seen_records = set()   # avoid listing the same NAMES record twice

    for i in range(num_thes):
        offset = i * THES_REC_SIZE
        rec    = _parse_thes(hier, offset)

        if not rec.is_leaf:
            continue

        path = _build_path(hier, offset)

        for hdp in rec.hdps:
            record_no = hdp            # leaf HDPs are NAMES record numbers
            if not (0 <= record_no < num_names):
                continue
            if record_no in seen_records:
                continue
            seen_records.add(record_no)

            name, item_type, address = _parse_names(names, record_no)
            entries.append(DatasetEntry(
                path      = path,
                name      = name,
                item_type = item_type,
                type_name = ITEM_TYPE_NAMES.get(item_type, f"Unknown({item_type})"),
                address   = address,
                record_no = record_no,
            ))

    entries.sort(key=lambda e: (e.path, e.name))
    return entries


# ── Output formatters ─────────────────────────────────────────────────────────

def _format_text(entries: list) -> str:
    lines     = []
    prev_path = None
    for e in entries:
        p = e.path_str()
        if p != prev_path:
            lines.append(f"\n{p}")
            prev_path = p
        lines.append(f"  [{e.type_name:<16s}] {e.name}")
    return "\n".join(lines)


def _format_csv(entries: list) -> str:
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["path", "name", "type", "address_hex", "record_no"])
    for e in entries:
        w.writerow([e.path_str(), e.name, e.type_name,
                    f"0x{e.address:08X}", e.record_no])
    return buf.getvalue()


def _format_json(entries: list) -> str:
    return json.dumps(
        [{"path": e.path, "name": e.name, "type": e.type_name,
          "address": e.address, "record_no": e.record_no}
         for e in entries],
        indent=2,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description="List all datasets on the BBC Domesday National disc",
    )
    parser.add_argument(
        "--data", default="data/NationalA",
        help="Path to NationalA data directory (default: data/NationalA)",
    )
    parser.add_argument(
        "--format", choices=["text", "csv", "json"], default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--type", dest="filter_type", metavar="TYPE",
        help='Filter by type name substring, e.g. "Essay", "Photo", "Grid map"',
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.exists():
        print(f"Error: data directory '{data_dir}' not found", file=sys.stderr)
        sys.exit(1)

    entries = extract_catalogue(data_dir)

    if args.filter_type:
        entries = [e for e in entries
                   if args.filter_type.lower() in e.type_name.lower()]

    if args.format == "text":
        print(_format_text(entries))
    elif args.format == "csv":
        print(_format_csv(entries), end="")
    elif args.format == "json":
        print(_format_json(entries))

    if args.format == "text":
        type_counts: dict = {}
        for e in entries:
            type_counts[e.type_name] = type_counts.get(e.type_name, 0) + 1
        print(f"\n── Summary {'─' * 40}")
        print(f"Total datasets: {len(entries)}")
        for t, c in sorted(type_counts.items()):
            print(f"  {t:<20s} {c:5d}")


if __name__ == "__main__":
    main()

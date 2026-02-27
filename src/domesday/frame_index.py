"""BBC Domesday Frame Index — look up all known metadata for any LaserDisc frame.

Supports both National and Community disc layouts (auto-detected).

National disc sources:
  1. Gallery views (VFS/GALLERY at offset 0, 320 views)
  2. Walk sub-datasets embedded in GALLERY (BRECON, SCOT, URBAN, etc.)
  3. Photo sets (catalogue type-8 items → DATA1/DATA2)
  4. Essay figure images (catalogue type-6/7 items → DATA1/DATA2)

Community disc sources:
  1. Map image frames (MAPDATA1 slot mapno fields)
  2. Data bundle frames (MAPDATA1 ptaddress / submap ptaddress fields)
  3. Community photos (photo frame numbers inside each data bundle)

CLI usage
---------
    # Single frame lookup (national or community)
    python -m domesday.frame_index --frame 803
    python -m domesday.frame_index --data data/CommN --frame 18803

    # Filter by record type
    python -m domesday.frame_index --type walk_view
    python -m domesday.frame_index --data data/CommN --type map_image

    # All known frames → JSON
    python -m domesday.frame_index --format json --output frame_index.json

    # All known frames → CSV
    python -m domesday.frame_index --format csv --output frames.csv
"""

from __future__ import annotations

import csv
import io
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from domesday.catalogue import DatasetEntry, extract_catalogue
from domesday.export import discover_walks
from domesday.parser import (
    decode_names_address,
    load_gallery_subdataset,
    parse_dataset,
    parse_essay,
    parse_photo_frames,
    parse_photo_set,
)

# Maximum bytes to read for one walk dataset (walk datasets are ≤26 KB)
_WALK_CHUNK = 65_536

# Community disc MAPDATA1 constants
_MAPDATA1_SLOT = 816   # bytes per fixed-size slot
_BUNDLE_FRAME  = 6144  # bytes per data bundle frame


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FrameRecord:
    frame: int
    record_type: str   # national: "gallery_view"|"walk_view"|"plan"|"photo"|"essay_figure"
                       # community: "map_image"|"data_bundle"|"community_photo"
    title: str
    path: list[str] = field(default_factory=list)  # thesaurus path (national only)

    # Walk / plan specific (national)
    walk_name: str | None = None
    view_number: int | None = None
    plan_number: int | None = None

    # Photo specific (national)
    short_caption: str | None = None
    long_caption: str | None = None
    photo_index: int | None = None
    photo_count: int | None = None

    # Essay specific (national)
    essay_title: str | None = None
    figure_page: int | None = None
    figure_index: int | None = None

    # Community disc specific
    map_level: int | None = None
    map_easting: int | None = None
    map_northing: int | None = None


# ---------------------------------------------------------------------------
# Disc-type detection
# ---------------------------------------------------------------------------


def _is_community_disc(data_dir: Path) -> bool:
    """Return True if data_dir is a community disc (has MAPDATA1, no GALLERY)."""
    return (data_dir / 'VFS' / 'MAPDATA1').exists()


# ---------------------------------------------------------------------------
# Source 1 — Gallery views (GALLERY at offset 0)  [National]
# ---------------------------------------------------------------------------


def _gallery_records(data_dir: Path) -> list[FrameRecord]:
    """Parse the main GALLERY dataset at offset 0 and return frame records."""
    gallery_path = data_dir / 'VFS' / 'GALLERY'
    gallery_bytes = gallery_path.read_bytes()
    ds = parse_dataset(gallery_bytes, gallery_path, byte_offset=0)

    records: list[FrameRecord] = []
    seen_plans: set[int] = set()

    for v, node in ds.nodes.items():
        records.append(FrameRecord(
            frame=node.frame,
            record_type="gallery_view",
            title="Gallery",
            walk_name="Gallery",
            view_number=v,
        ))
        if node.plan is not None:
            plan_frame = ds.get_plan_frame(node.plan.plan_number)
            if plan_frame not in seen_plans:
                seen_plans.add(plan_frame)
                records.append(FrameRecord(
                    frame=plan_frame,
                    record_type="plan",
                    title="Gallery",
                    walk_name="Gallery",
                    plan_number=node.plan.plan_number,
                ))

    return records


# ---------------------------------------------------------------------------
# Source 2 — Walk sub-datasets (embedded in GALLERY)  [National]
# ---------------------------------------------------------------------------


def _walk_records(data_dir: Path, catalogue_entries: list[DatasetEntry]) -> list[FrameRecord]:
    """Return frame records for the 9 walk environments embedded in the GALLERY file."""
    gallery_path = data_dir / 'VFS' / 'GALLERY'

    walk_path_map: dict[str, list[str]] = {}
    for e in catalogue_entries:
        if e.item_type == 9:
            key = e.name.lower().split()[0] if e.name else ''
            if key:
                walk_path_map[key] = e.path

    walks = discover_walks(gallery_path)
    records: list[FrameRecord] = []

    for walk_meta in walks:
        off = walk_meta['gallery_offset']
        name: str = walk_meta['name']
        path = walk_path_map.get(name, [])

        try:
            sub_ds = load_gallery_subdataset(gallery_path, off)
        except Exception:
            continue

        seen_plans: set[int] = set()
        for v, node in sub_ds.nodes.items():
            records.append(FrameRecord(
                frame=node.frame,
                record_type="walk_view",
                title=name,
                path=path,
                walk_name=name,
                view_number=v,
            ))
            if node.plan is not None:
                plan_frame = sub_ds.get_plan_frame(node.plan.plan_number)
                if plan_frame not in seen_plans:
                    seen_plans.add(plan_frame)
                    records.append(FrameRecord(
                        frame=plan_frame,
                        record_type="plan",
                        title=name,
                        path=path,
                        walk_name=name,
                        plan_number=node.plan.plan_number,
                    ))

    return records


# ---------------------------------------------------------------------------
# Source 3 — Photo sets (catalogue type 8)  [National]
# ---------------------------------------------------------------------------


def _photo_records(data_dir: Path, catalogue_entries: list[DatasetEntry]) -> list[FrameRecord]:
    """Extract photo frame records from all type-8 catalogue entries."""
    data1 = data_dir / 'VFS' / 'DATA1'
    data2 = data_dir / 'VFS' / 'DATA2'

    records: list[FrameRecord] = []

    for entry in catalogue_entries:
        if entry.item_type != 8:
            continue

        is_data2, byte_off = decode_names_address(entry.address)
        data_path = data2 if is_data2 else data1

        try:
            result = parse_photo_set(data_path, byte_off)
        except Exception:
            continue

        frames = result['frames']
        captions = result['captions']
        descriptions = result['descriptions']

        for i, frame in enumerate(frames):
            records.append(FrameRecord(
                frame=frame,
                record_type="photo",
                title=entry.name,
                path=entry.path,
                short_caption=captions[i] if i < len(captions) else None,
                long_caption=descriptions[i] if i < len(descriptions) else None,
                photo_index=i,
                photo_count=len(frames),
            ))

    return records


# ---------------------------------------------------------------------------
# Source 4 — Essay figure frames (catalogue types 6 and 7)  [National]
# ---------------------------------------------------------------------------


def _essay_records(data_dir: Path, catalogue_entries: list[DatasetEntry]) -> list[FrameRecord]:
    """Extract essay figure frame records from type-6/7 catalogue entries."""
    data1 = data_dir / 'VFS' / 'DATA1'
    data2 = data_dir / 'VFS' / 'DATA2'

    records: list[FrameRecord] = []

    for entry in catalogue_entries:
        if entry.item_type not in (6, 7):
            continue

        is_data2, byte_off = decode_names_address(entry.address)
        data_path = data2 if is_data2 else data1

        try:
            result = parse_essay(data_path, byte_off)
        except Exception:
            continue

        titles = result.get('titles', [])
        essay_title = titles[0] if titles else entry.name
        figures = result.get('figures', [])

        for i, fig in enumerate(figures):
            if fig is None:
                continue

            fig_is_data2, fig_off = decode_names_address(fig['address'])
            fig_data_path = data2 if fig_is_data2 else data1

            try:
                frame_list = parse_photo_frames(fig_data_path, fig_off)
            except Exception:
                continue

            for frame in frame_list:
                records.append(FrameRecord(
                    frame=frame,
                    record_type="essay_figure",
                    title=essay_title,
                    path=entry.path,
                    essay_title=essay_title,
                    figure_page=fig['page_num'],
                    figure_index=i,
                ))

    return records


# ---------------------------------------------------------------------------
# Source 5 — Community disc: MAPDATA1 maps + data bundles + photos  [Community]
# ---------------------------------------------------------------------------


def _iter_mapdata1_slots(mapdata: bytes):
    """Yield parsed dicts for each valid slot in a MAPDATA1 file."""
    for i in range(len(mapdata) // _MAPDATA1_SLOT):
        off = i * _MAPDATA1_SLOT
        blen = struct.unpack_from('<H', mapdata, off)[0]
        if blen < 18 or blen > _MAPDATA1_SLOT:
            continue

        mapno    = struct.unpack_from('<H', mapdata, off + 2)[0]
        easting  = struct.unpack_from('<H', mapdata, off + 4)[0]
        northing = struct.unpack_from('<H', mapdata, off + 6)[0]
        level    = mapdata[off + 10]
        flags    = mapdata[off + 11]
        ptaddress = struct.unpack_from('<H', mapdata, off + 12)[0]
        M        = mapdata[off + 14]
        N        = mapdata[off + 15]
        mn       = M * N

        submap_ptaddrs: list[int] = []
        if mn > 0 and 18 + 3 * mn <= blen:
            pt_off = off + 18 + mn   # skip MN submap indices (1 byte each)
            for j in range(mn):
                pt = struct.unpack_from('<H', mapdata, pt_off + j * 2)[0]
                if pt:
                    submap_ptaddrs.append(pt)

        yield {
            'mapno': mapno,
            'easting': easting,
            'northing': northing,
            'level': level,
            'flags': flags,
            'ptaddress': ptaddress,
            'submap_ptaddrs': submap_ptaddrs,
        }


def _community_records(data_dir: Path) -> list[FrameRecord]:
    """Build frame records from a community disc MAPDATA1 + data bundles."""
    mapdata = (data_dir / 'VFS' / 'MAPDATA1').read_bytes()
    data1_path = data_dir / 'VFS' / 'DATA1'
    data2_path = data_dir / 'VFS' / 'DATA2'

    d1_frames = data1_path.stat().st_size // _BUNDLE_FRAME
    d2_frames = data2_path.stat().st_size // _BUNDLE_FRAME

    slots = list(_iter_mapdata1_slots(mapdata))

    # Discover DATA1 base ptaddress from the minimum non-zero ptaddress in MAPDATA1.
    # DATA1 stores bundles sequentially starting at that base frame number;
    # DATA2 continues immediately after.
    all_ptaddrs: set[int] = set()
    for s in slots:
        if s['ptaddress']:
            all_ptaddrs.add(s['ptaddress'])
        all_ptaddrs.update(s['submap_ptaddrs'])

    if not all_ptaddrs:
        return [FrameRecord(frame=s['mapno'], record_type='map_image',
                            title=f"Map L{s['level']}", map_level=s['level'],
                            map_easting=s['easting'], map_northing=s['northing'])
                for s in slots]

    d1_base = min(all_ptaddrs)
    d2_base = d1_base + d1_frames

    records: list[FrameRecord] = []
    seen_bundles: set[int] = set()
    seen_photos: set[int] = set()

    with open(data1_path, 'rb') as f1, open(data2_path, 'rb') as f2:

        def read_bundle(ptaddr: int) -> bytes | None:
            if d1_base <= ptaddr < d2_base:
                f1.seek((ptaddr - d1_base) * _BUNDLE_FRAME)
                return f1.read(_BUNDLE_FRAME)
            elif d2_base <= ptaddr < d2_base + d2_frames:
                f2.seek((ptaddr - d2_base) * _BUNDLE_FRAME)
                return f2.read(_BUNDLE_FRAME)
            return None   # frame is on the other disc side

        def decode_bundle(bundle: bytes) -> dict:
            """Extract header fields and photo frame list from a data bundle."""
            level   = bundle[0]
            picoff  = struct.unpack_from('<H', bundle, 2)[0]
            map_no  = struct.unpack_from('<H', bundle, 6)[0]
            east    = struct.unpack_from('<H', bundle, 10)[0]
            north   = struct.unpack_from('<H', bundle, 12)[0]
            photos: list[dict] = []
            if 0 < picoff < _BUNDLE_FRAME - 2:
                npics_raw   = struct.unpack_from('<H', bundle, picoff)[0]
                npics       = npics_raw & 0x7F
                short_start = picoff + 2 + 2 * npics
                for n in range(1, npics + 1):   # BCPL 1-indexed
                    f_off = picoff + n * 2
                    if f_off + 2 > _BUNDLE_FRAME:
                        break
                    fr = struct.unpack_from('<H', bundle, f_off)[0]
                    cap_off = short_start + (n - 1) * 30
                    if fr and cap_off + 30 <= _BUNDLE_FRAME:
                        caption = (bundle[cap_off:cap_off + 30]
                                   .rstrip(b' \x00')
                                   .decode('latin-1', errors='replace'))
                        photos.append({'frame': fr, 'caption': caption})
            return {
                'level': level, 'map_no': map_no,
                'easting': east, 'northing': north,
                'photos': photos,
            }

        for slot in slots:
            mapno    = slot['mapno']
            level    = slot['level']
            easting  = slot['easting']
            northing = slot['northing']

            # Map image frame (the video frame showing the map)
            records.append(FrameRecord(
                frame=mapno,
                record_type='map_image',
                title=f'Map L{level}',
                map_level=level,
                map_easting=easting,
                map_northing=northing,
            ))

            # Data bundle frames (main + per-submap)
            ptaddrs = []
            if slot['ptaddress']:
                ptaddrs.append(slot['ptaddress'])
            ptaddrs.extend(slot['submap_ptaddrs'])

            for ptaddr in ptaddrs:
                if ptaddr in seen_bundles:
                    continue
                seen_bundles.add(ptaddr)

                bundle = read_bundle(ptaddr)
                if bundle is None:
                    continue

                info = decode_bundle(bundle)
                records.append(FrameRecord(
                    frame=ptaddr,
                    record_type='data_bundle',
                    title=f"L{info['level']} data bundle",
                    map_level=info['level'],
                    map_easting=info['easting'],
                    map_northing=info['northing'],
                ))

                # Individual photo frames within this bundle
                for photo in info['photos']:
                    pf = photo['frame']
                    if pf not in seen_photos:
                        seen_photos.add(pf)
                        records.append(FrameRecord(
                            frame=pf,
                            record_type='community_photo',
                            title=photo['caption'] or 'Community photo',
                            short_caption=photo['caption'] or None,
                            map_level=info['level'],
                            map_easting=info['easting'],
                            map_northing=info['northing'],
                        ))

    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_frame_index(data_dir: Path) -> list[FrameRecord]:
    """Build the complete frame index for all known frames on the disc.

    Auto-detects disc type (National vs Community) from VFS file inventory.
    Returns a deduplicated list of FrameRecord objects sorted by frame number.
    Deduplication key: (record_type, frame, title).
    """
    if _is_community_disc(data_dir):
        all_records = _community_records(data_dir)
    else:
        entries = extract_catalogue(data_dir)
        all_records = []
        all_records.extend(_gallery_records(data_dir))
        all_records.extend(_walk_records(data_dir, entries))
        all_records.extend(_photo_records(data_dir, entries))
        all_records.extend(_essay_records(data_dir, entries))

    seen: set[tuple] = set()
    unique: list[FrameRecord] = []
    for rec in all_records:
        key = (rec.record_type, rec.frame, rec.title)
        if key not in seen:
            seen.add(key)
            unique.append(rec)

    unique.sort(key=lambda r: (r.frame, r.record_type))
    return unique


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_frame_text(records: list[FrameRecord]) -> str:
    """Pretty-print metadata for a single frame lookup."""
    if not records:
        return "Frame not found in index."

    lines: list[str] = []
    for rec in records:
        path_str = ' > '.join(rec.path) if rec.path else ''
        lines.append(f"Frame {rec.frame}")

        if rec.record_type == 'gallery_view':
            lines.append(f"  [gallery_view] Gallery")
            lines.append(f"  View:   {rec.view_number}")

        elif rec.record_type == 'walk_view':
            lines.append(f"  [walk_view] {rec.walk_name}")
            if path_str:
                lines.append(f"  Path:   {path_str}")
            lines.append(f"  View:   {rec.view_number}")

        elif rec.record_type == 'plan':
            lines.append(f"  [plan] {rec.walk_name or rec.title}")
            if path_str:
                lines.append(f"  Path:   {path_str}")
            lines.append(f"  Plan:   {rec.plan_number}")

        elif rec.record_type == 'photo':
            lines.append(f"  [photo] {rec.title}")
            if path_str:
                lines.append(f"  Path:   {path_str}")
            if rec.photo_index is not None and rec.photo_count is not None:
                lines.append(f"  Photo:  {rec.photo_index + 1} of {rec.photo_count}")
            if rec.short_caption:
                lines.append(f"  Short:  \"{rec.short_caption}\"")
            if rec.long_caption:
                lines.append(f"  Long:   \"{rec.long_caption}\"")

        elif rec.record_type == 'essay_figure':
            lines.append(f"  [essay_figure] {rec.essay_title}")
            if path_str:
                lines.append(f"  Path:   {path_str}")
            lines.append(f"  Page:   {rec.figure_page}")
            lines.append(f"  Figure: {rec.figure_index}")

        elif rec.record_type == 'map_image':
            lines.append(f"  [map_image] L{rec.map_level} map")
            lines.append(f"  Easting:  {rec.map_easting} km")
            lines.append(f"  Northing: {rec.map_northing} km")

        elif rec.record_type == 'data_bundle':
            lines.append(f"  [data_bundle] L{rec.map_level}")
            lines.append(f"  Easting:  {rec.map_easting} km")
            lines.append(f"  Northing: {rec.map_northing} km")

        elif rec.record_type == 'community_photo':
            lines.append(f"  [community_photo] L{rec.map_level}")
            lines.append(f"  Easting:  {rec.map_easting} km")
            lines.append(f"  Northing: {rec.map_northing} km")
            if rec.short_caption:
                lines.append(f"  Caption: \"{rec.short_caption}\"")

        lines.append('')

    return '\n'.join(lines).rstrip()


def _format_json(records: list[FrameRecord]) -> str:
    def _to_dict(r: FrameRecord) -> dict:
        d: dict = {
            'frame': r.frame,
            'record_type': r.record_type,
            'title': r.title,
            'path': r.path,
        }
        if r.record_type in ('gallery_view', 'walk_view'):
            d['walk_name'] = r.walk_name
            d['view_number'] = r.view_number
        if r.record_type == 'plan':
            d['walk_name'] = r.walk_name
            d['plan_number'] = r.plan_number
        if r.record_type == 'photo':
            d['short_caption'] = r.short_caption
            d['long_caption'] = r.long_caption
            d['photo_index'] = r.photo_index
            d['photo_count'] = r.photo_count
        if r.record_type == 'essay_figure':
            d['essay_title'] = r.essay_title
            d['figure_page'] = r.figure_page
            d['figure_index'] = r.figure_index
        if r.record_type in ('map_image', 'data_bundle', 'community_photo'):
            d['map_level'] = r.map_level
            d['map_easting'] = r.map_easting
            d['map_northing'] = r.map_northing
        if r.record_type == 'community_photo':
            d['short_caption'] = r.short_caption
        return d

    return json.dumps([_to_dict(r) for r in records], indent=2)


def _format_csv(records: list[FrameRecord]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        'frame', 'record_type', 'title', 'path',
        'walk_name', 'view_number', 'plan_number',
        'short_caption', 'long_caption', 'photo_index', 'photo_count',
        'essay_title', 'figure_page', 'figure_index',
        'map_level', 'map_easting', 'map_northing',
    ])
    for r in records:
        w.writerow([
            r.frame, r.record_type, r.title, ' > '.join(r.path),
            r.walk_name or '',
            r.view_number if r.view_number is not None else '',
            r.plan_number if r.plan_number is not None else '',
            r.short_caption or '',
            r.long_caption or '',
            r.photo_index if r.photo_index is not None else '',
            r.photo_count if r.photo_count is not None else '',
            r.essay_title or '',
            r.figure_page if r.figure_page is not None else '',
            r.figure_index if r.figure_index is not None else '',
            r.map_level if r.map_level is not None else '',
            r.map_easting if r.map_easting is not None else '',
            r.map_northing if r.map_northing is not None else '',
        ])
    return buf.getvalue()


def _format_summary(records: list[FrameRecord]) -> str:
    """One line per record for bulk/filtered display."""
    if not records:
        return "No records found."
    lines = []
    for r in records:
        path_str = ' > '.join(r.path) if r.path else ''
        lines.append(f"Frame {r.frame:6d}  [{r.record_type:<16s}]  {r.title}  {path_str}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(
        description='BBC Domesday frame index — look up metadata for disc frames '
                    '(National and Community discs supported)',
    )
    p.add_argument('--data', default='data/NationalA',
                   help='Path to disc data directory (default: data/NationalA)')
    p.add_argument('--frame', type=int, default=None,
                   help='Look up a single frame number')
    p.add_argument('--type', dest='filter_type', metavar='TYPE',
                   help='Filter by record type: '
                        'gallery_view, walk_view, plan, photo, essay_figure '
                        '(national); '
                        'map_image, data_bundle, community_photo '
                        '(community)')
    p.add_argument('--format', choices=['text', 'json', 'csv'], default='text',
                   help='Output format (default: text; --frame uses pretty layout for text)')
    p.add_argument('--output', metavar='FILE', default=None,
                   help='Write output to FILE instead of stdout')
    args = p.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.exists():
        print(f"Error: data directory '{data_dir}' not found", file=sys.stderr)
        sys.exit(1)

    disc_type = 'community' if _is_community_disc(data_dir) else 'national'
    print(f"Building frame index ({disc_type} disc)...", file=sys.stderr)
    records = build_frame_index(data_dir)
    print(f"Total records: {len(records)}", file=sys.stderr)

    if args.frame is not None:
        records = [r for r in records if r.frame == args.frame]
    if args.filter_type:
        records = [r for r in records if r.record_type == args.filter_type]

    if args.format == 'json':
        output = _format_json(records)
    elif args.format == 'csv':
        output = _format_csv(records)
    elif args.frame is not None:
        output = _format_frame_text(records)
    else:
        output = _format_summary(records)

    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()

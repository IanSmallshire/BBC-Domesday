"""BBC Domesday Frame Index — look up all known metadata for any LaserDisc frame.

Aggregates frame numbers from four sources:
  1. Gallery views (VFS/GALLERY at offset 0, 320 views)
  2. Walk sub-datasets embedded in GALLERY (BRECON, SCOT, URBAN, etc.)
  3. Photo sets (catalogue type-8 items → DATA1/DATA2)
  4. Essay figure images (catalogue type-6/7 items → DATA1/DATA2)

CLI usage
---------
    # Single frame lookup
    python -m domesday.frame_index --frame 803

    # Filter by record type
    python -m domesday.frame_index --type walk_view

    # All known frames → JSON
    python -m domesday.frame_index --format json --output frame_index.json

    # All known frames → CSV
    python -m domesday.frame_index --format csv --output frames.csv
"""

from __future__ import annotations

import csv
import io
import json
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


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FrameRecord:
    frame: int
    record_type: str         # "gallery_view" | "walk_view" | "plan" | "photo" | "essay_figure"
    title: str               # walk name, photo set name, or essay title
    path: list[str] = field(default_factory=list)  # thesaurus path from catalogue

    # Walk / plan specific
    walk_name: str | None = None
    view_number: int | None = None   # 1-based view index within dataset
    plan_number: int | None = None   # for "plan" records only

    # Photo specific
    short_caption: str | None = None
    long_caption: str | None = None
    photo_index: int | None = None   # 0-based index within photo set
    photo_count: int | None = None   # total photos in the set

    # Essay specific
    essay_title: str | None = None
    figure_page: int | None = None   # essay page the figure appears on
    figure_index: int | None = None  # 0-based index in figure list


# ---------------------------------------------------------------------------
# Source 1 — Gallery views (GALLERY at offset 0)
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
# Source 2 — Walk sub-datasets (embedded in GALLERY)
# ---------------------------------------------------------------------------


def _walk_records(data_dir: Path, catalogue_entries: list[DatasetEntry]) -> list[FrameRecord]:
    """Return frame records for the 9 walk environments embedded in the GALLERY file.

    Walk names come from the binary header (via discover_walks).  If a matching
    catalogue entry exists for the walk name we enrich the record with the
    thesaurus path; otherwise path is left empty.
    """
    gallery_path = data_dir / 'VFS' / 'GALLERY'

    # Build a name→path lookup from type-9 catalogue entries (best-effort match)
    walk_path_map: dict[str, list[str]] = {}
    for e in catalogue_entries:
        if e.item_type == 9:
            # Normalise to lower-case first word for fuzzy matching with walk binary names
            key = e.name.lower().split()[0] if e.name else ''
            if key:
                walk_path_map[key] = e.path

    walks = discover_walks(gallery_path)
    records: list[FrameRecord] = []

    for walk_meta in walks:
        off = walk_meta['gallery_offset']
        name: str = walk_meta['name']  # lowercase first word, e.g. "brecon"
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
# Source 3 — Photo sets (catalogue type 8)
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
# Source 4 — Essay figure frames (catalogue types 6 and 7)
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
# Public API
# ---------------------------------------------------------------------------


def build_frame_index(data_dir: Path) -> list[FrameRecord]:
    """Build the complete frame index for all known frames on the disc.

    Returns a deduplicated list of FrameRecord objects sorted by frame number.
    Deduplication key: (record_type, frame, title).
    """
    entries = extract_catalogue(data_dir)

    all_records: list[FrameRecord] = []
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
        ])
    return buf.getvalue()


def _format_summary(records: list[FrameRecord]) -> str:
    """One line per record for bulk/filtered display."""
    if not records:
        return "No records found."
    lines = []
    for r in records:
        path_str = ' > '.join(r.path) if r.path else ''
        lines.append(f"Frame {r.frame:6d}  [{r.record_type:<14s}]  {r.title}  {path_str}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(
        description='BBC Domesday frame index — look up metadata for disc frames',
    )
    p.add_argument('--data', default='data/NationalA',
                   help='Path to NationalA data directory (default: data/NationalA)')
    p.add_argument('--frame', type=int, default=None,
                   help='Look up a single frame number')
    p.add_argument('--type', dest='filter_type', metavar='TYPE',
                   help='Filter by record type: gallery_view, walk_view, plan, photo, essay_figure')
    p.add_argument('--format', choices=['text', 'json', 'csv'], default='text',
                   help='Output format (default: text; --frame uses pretty layout for text)')
    p.add_argument('--output', metavar='FILE', default=None,
                   help='Write output to FILE instead of stdout')
    args = p.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.exists():
        print(f"Error: data directory '{data_dir}' not found", file=sys.stderr)
        sys.exit(1)

    print("Building frame index...", file=sys.stderr)
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

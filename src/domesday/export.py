"""Export each walk sub-dataset from the GALLERY file to individual JSON files.

Usage:
    python -m domesday.export \
        --gallery data/NationalA/VFS/GALLERY \
        --output  exports/walks

    # With full detail metadata (titles, types, frames, page counts):
    python -m domesday.export --with-details

Produces one JSON file per dataset (gallery + all 9 walks by default):
    exports/walks/gallery.json
    exports/walks/brecon.json
    ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from domesday.models import DetailIcon, Node, WalkDataset
from domesday.parser import (
    NAMES_TYPE_LABELS,
    decode_names_address,
    load_gallery,
    load_gallery_subdataset,
    parse_closeup_frames,
    parse_dataset,
    parse_essay,
    parse_gallery_item,
    parse_names_record,
    parse_photo_set,
    probe_data_type,
)


def _walk_name(header_bytes: bytes) -> str:
    """Extract a filesystem-safe walk name from the first 16 bytes of a sub-dataset."""
    raw = header_bytes[:16].split(b"\x00")[0].decode("ascii", errors="replace").strip()
    # Keep only the first word (e.g. "BRECON GAL 3.0" → "brecon")
    first_word = re.split(r"[\s_]", raw)[0].lower()
    return first_word or "walk"


def _resolve_figure_meta(fig: dict, data1: Path | None, data2: Path | None) -> dict:
    """Resolve metadata for one essay figure (address → photo set or essay summary).

    Each figure dict has 'address' (raw 32-bit) and 'page_num' (essay page it appears on).

    Returns a dict with address, page_num, type, type_name and content fields.
    Never recurses into nested figure_details (one level only).
    Silently returns {} on any error.

    Note: probe_data_type uses a heuristic (num_pics in 1-200). Photo sets with
    more than 200 frames are misclassified as 'essay'; they are still parsed
    correctly via parse_photo_set and returned as type=8.
    """
    try:
        is_data2, file_offset = decode_names_address(fig['address'])
        data_path = data2 if is_data2 else data1
        if data_path is None:
            return {}
        base: dict = {'address': fig['address'], 'page_num': fig['page_num']}
        kind = probe_data_type(data_path, file_offset)
        if kind == 'photo':
            photo = parse_photo_set(data_path, file_offset)
            return {**base, 'type': 8, 'type_name': 'Photo',
                    'frames': photo['frames'], 'frame_count': len(photo['frames'])}
        else:
            # probe_data_type says 'essay'; parse accordingly.
            # Large photo sets (> 200 frames) are misclassified here — their data
            # will still be read but with nonsensical essay fields.
            essay = parse_essay(data_path, file_offset)
            fig_count = len([f for f in essay['figures'] if f is not None])
            return {**base, 'type': 6, 'type_name': 'Text',
                    'page_count': essay['num_pages'], 'figure_count': fig_count}
    except Exception:
        return {}


def _resolve_detail_meta(
    gallery_data: bytes,
    gallery_offset: int,
    ds: WalkDataset,
    icon: DetailIcon,
    names_data: bytes | None,
    data1: Path | None,
    data2: Path | None,
) -> dict:
    """Resolve full metadata for one detail icon.

    Gallery mode (syslev==1): looks up NAMES record; fetches frame list for
    photo sets (type 8) and page/figure counts plus figure_details for essays (types 6/7).

    Walk mode (syslev!=1): reads the closeup frame chain from the detail table.

    Returns an enrichment dict to merge into the base icon dict.
    Silently returns {} on any error (partial data → omit extra keys rather than crash).
    """
    try:
        ds_data = gallery_data[gallery_offset:]

        if ds.syslev == 1:
            if names_data is None:
                return {}
            record_index = parse_gallery_item(ds_data, ds.dtable_byte, icon.item_offset)
            rec = parse_names_record(names_data, record_index)
            type_name = NAMES_TYPE_LABELS.get(rec['type'], 'Unknown')
            result: dict = {
                'title': rec['title'],
                'type': rec['type'],
                'type_name': type_name,
            }
            if rec['type'] == 8:  # Photo set
                is_data2, file_offset = decode_names_address(rec['address'])
                data_path = data2 if is_data2 else data1
                if data_path is not None:
                    photo = parse_photo_set(data_path, file_offset)
                    result['frames'] = photo['frames']
                    result['frame_count'] = len(photo['frames'])
            elif rec['type'] in (6, 7):  # Essay / Picture Essay
                is_data2, file_offset = decode_names_address(rec['address'])
                data_path = data2 if is_data2 else data1
                if data_path is not None:
                    essay = parse_essay(data_path, file_offset)
                    non_null_figs = [f for f in essay['figures'] if f is not None]
                    result['page_count'] = essay['num_pages']
                    result['figure_count'] = len(non_null_figs)
                    if non_null_figs:
                        figure_details = []
                        for fig in non_null_figs:
                            fd = _resolve_figure_meta(fig, data1, data2)
                            if fd:
                                figure_details.append(fd)
                        if figure_details:
                            result['figure_details'] = figure_details
            return result
        else:
            # Walk mode: closeup frame chain
            frames = parse_closeup_frames(
                ds_data, ds.dtable_byte, icon.item_offset, ds.base_view
            )
            return {
                'title': '',
                'type': -1,
                'type_name': 'closeup',
                'frames': frames,
                'frame_count': len(frames),
            }
    except Exception:
        return {}


def _node_to_export(
    node: Node,
    dataset: WalkDataset,
    detail_extra: dict[int, dict] | None = None,
) -> dict:
    d = asdict(node)
    d["plan_frame"] = dataset.get_plan_frame(node.plan.plan_number) if node.plan else None
    if detail_extra is not None and node.details:
        new_details = []
        for icon in node.details:
            entry = asdict(icon)
            extra = detail_extra.get(icon.item_offset, {})
            entry.update(extra)
            new_details.append(entry)
        d["details"] = new_details
    return d


def discover_walks(gallery_path: Path) -> list[dict]:
    """Return metadata for every linked sub-dataset found in the gallery."""
    data = gallery_path.read_bytes()
    ds = load_gallery(gallery_path)

    seen: dict[int, dict] = {}
    for view, node in sorted(ds.nodes.items()):
        if node.linked_dataset and node.link_target:
            off = node.link_target.byte_offset
            if off not in seen:
                name = _walk_name(data[off : off + 16])
                seen[off] = {
                    "name": name,
                    "gallery_offset": off,
                    "gallery_view": view,
                }
    return list(seen.values())


def export_dataset(
    gallery_path: Path,
    offset: int,
    name: str,
    gallery_view: int | None,
    output_dir: Path,
    names_data: bytes | None = None,
    data1: Path | None = None,
    data2: Path | None = None,
    with_details: bool = False,
) -> Path:
    """Parse and export one dataset as JSON.  Returns the output path."""
    gallery_data = gallery_path.read_bytes()
    ds = parse_dataset(gallery_data, gallery_path, offset)

    detail_extra: dict[int, dict] | None = None
    if with_details:
        detail_extra = {}
        for node in ds.nodes.values():
            for icon in node.details:
                if icon.item_offset not in detail_extra:
                    meta = _resolve_detail_meta(
                        gallery_data, offset, ds, icon, names_data, data1, data2
                    )
                    if meta:
                        detail_extra[icon.item_offset] = meta

    payload = {
        "name": name,
        "gallery_offset": offset,
        "gallery_view": gallery_view,
        "initial_view": ds.initial_view,
        "base_view": ds.base_view,
        "base_plan": ds.base_plan,
        "syslev": ds.syslev,
        "node_count": len(ds.nodes),
        "nodes": {str(v): _node_to_export(n, ds, detail_extra) for v, n in ds.nodes.items()},
    }

    out_path = output_dir / f"{name}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Export Domesday walk datasets to JSON")
    p.add_argument(
        "--gallery",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_GALLERY", "data/NationalA/VFS/GALLERY")),
        help="Path to the GALLERY data file",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("exports/walks"),
        help="Directory to write JSON files into",
    )
    p.add_argument(
        "--no-gallery",
        dest="include_gallery",
        action="store_false",
        default=True,
        help="Skip exporting the main gallery dataset (walk sub-datasets only)",
    )
    p.add_argument(
        "--names",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_NAMES", "data/NationalA/VFS/NAMES")),
        help="Path to the NAMES file (required for --with-details on gallery)",
    )
    p.add_argument(
        "--data1",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_DATA1", "data/NationalA/VFS/DATA1")),
        help="Path to DATA1 file (required for photo/essay detail with --with-details)",
    )
    p.add_argument(
        "--data2",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_DATA2", "data/NationalA/VFS/DATA2")),
        help="Path to DATA2 file (required for photo/essay detail with --with-details)",
    )
    p.add_argument(
        "--with-details",
        action="store_true",
        default=False,
        help=(
            "Resolve detail dot metadata: title, type, type_name, frame list (photos/closeups), "
            "page/figure counts (essays). Requires --names, --data1, --data2."
        ),
    )
    args = p.parse_args()

    if not args.gallery.exists():
        raise SystemExit(f"GALLERY file not found: {args.gallery}")

    # Load auxiliary files if --with-details is requested
    names_data: bytes | None = None
    data1: Path | None = None
    data2: Path | None = None
    if args.with_details:
        if args.names.exists():
            names_data = args.names.read_bytes()
        else:
            print(f"  Warning: NAMES file not found at {args.names} — gallery title lookup disabled")
        data1 = args.data1 if args.data1.exists() else None
        data2 = args.data2 if args.data2.exists() else None
        if data1 is None:
            print(f"  Warning: DATA1 not found at {args.data1} — photo/essay detail disabled for DATA1")
        if data2 is None:
            print(f"  Warning: DATA2 not found at {args.data2} — photo/essay detail disabled for DATA2")

    args.output.mkdir(parents=True, exist_ok=True)

    walks = discover_walks(args.gallery)
    if args.include_gallery:
        walks = [{"name": "gallery", "gallery_offset": 0, "gallery_view": None}] + walks

    for meta in walks:
        out = export_dataset(
            gallery_path=args.gallery,
            offset=meta["gallery_offset"],
            name=meta["name"],
            gallery_view=meta["gallery_view"],
            output_dir=args.output,
            names_data=names_data,
            data1=data1,
            data2=data2,
            with_details=args.with_details,
        )
        print(f"  Wrote {out}  ({out.stat().st_size:,} bytes)")

    print(f"\nExported {len(walks)} dataset(s) to {args.output}/")


if __name__ == "__main__":
    main()

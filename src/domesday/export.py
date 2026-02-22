"""Export each walk sub-dataset from the GALLERY file to individual JSON files.

Usage:
    python -m domesday.export \
        --gallery data/NationalA/VFS/GALLERY \
        --output  exports/walks

Produces one JSON file per walk plus the main gallery:
    exports/walks/gallery.json
    exports/walks/brecon.json
    ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
from dataclasses import asdict
from pathlib import Path

from domesday.models import Node, WalkDataset
from domesday.parser import load_gallery, load_gallery_subdataset


def _walk_name(header_bytes: bytes) -> str:
    """Extract a filesystem-safe walk name from the first 16 bytes of a sub-dataset."""
    raw = header_bytes[:16].split(b"\x00")[0].decode("ascii", errors="replace").strip()
    # Keep only the first word (e.g. "BRECON GAL 3.0" → "brecon")
    first_word = re.split(r"[\s_]", raw)[0].lower()
    return first_word or "walk"


def _node_to_export(node: Node, dataset: WalkDataset) -> dict:
    d = asdict(node)
    d["plan_frame"] = dataset.get_plan_frame(node.plan.plan_number) if node.plan else None
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
) -> Path:
    """Parse and export one dataset as JSON.  Returns the output path."""
    if offset == 0:
        ds = load_gallery(gallery_path)
    else:
        ds = load_gallery_subdataset(gallery_path, offset)

    payload = {
        "name": name,
        "gallery_offset": offset,
        "gallery_view": gallery_view,
        "initial_view": ds.initial_view,
        "base_view": ds.base_view,
        "base_plan": ds.base_plan,
        "syslev": ds.syslev,
        "node_count": len(ds.nodes),
        "nodes": {str(v): _node_to_export(n, ds) for v, n in ds.nodes.items()},
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
        "--gallery-too",
        action="store_true",
        help="Also export the main gallery dataset",
    )
    args = p.parse_args()

    if not args.gallery.exists():
        raise SystemExit(f"GALLERY file not found: {args.gallery}")

    args.output.mkdir(parents=True, exist_ok=True)

    walks = discover_walks(args.gallery)
    if args.gallery_too:
        walks = [{"name": "gallery", "gallery_offset": 0, "gallery_view": None}] + walks

    for meta in walks:
        out = export_dataset(
            gallery_path=args.gallery,
            offset=meta["gallery_offset"],
            name=meta["name"],
            gallery_view=meta["gallery_view"],
            output_dir=args.output,
        )
        print(f"  Wrote {out}  ({out.stat().st_size:,} bytes)")

    print(f"\nExported {len(walks)} dataset(s) to {args.output}/")


if __name__ == "__main__":
    main()

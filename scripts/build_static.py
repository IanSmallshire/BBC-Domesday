#!/usr/bin/env python3
"""Build a self-contained static site for a Domesday walk (gallery sub-dataset).

Usage:
    .venv/bin/python scripts/build_static.py --disc NationalA --walk brecon

Generates:
    dist/NationalA/brecon/
        index.html      — static SPA (copy of src/domesday/static/index-static.html)
        walk.json       — full node graph with pre-computed nav + detail frames
        frames/NN/      — only the JPEG frames referenced by this walk
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

# Allow running without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from domesday.parser import leftof, load_gallery_subdataset, parse_closeup_frames, rightof
from domesday.export import discover_walks


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------


def _opposite(v: int) -> int:
    """Turn 180°: leftof applied four times."""
    for _ in range(4):
        v = leftof(v)
    return v


def _build_nav(v: int, nodes: dict) -> dict:
    """Pre-compute all six navigation outcomes for view v.

    Returns a dict with keys: forward, back, left, right, turn_left, turn_right.
    Values are view numbers (int) or None for dead-ends.
    """
    node = nodes[v]

    # ── forward ──────────────────────────────────────────────────────────────
    # If the forward link is into another dataset we can't follow it statically.
    if node.forward is not None and not node.linked_dataset:
        fwd_view = node.forward if node.forward in nodes else None
    else:
        fwd_view = None

    # ── back: opposite(v) → its forward → end up there ───────────────────────
    opp = _opposite(v)
    opp_node = nodes.get(opp)
    if (opp_node is not None
            and not opp_node.linked_dataset
            and opp_node.forward is not None
            and opp_node.forward in nodes):
        back_view = opp_node.forward
    else:
        back_view = None

    # ── right: rightof(rightof(v)) → forward → leftof(leftof(result)) ────────
    rr = rightof(rightof(v))
    rr_node = nodes.get(rr)
    if (rr_node is not None
            and not rr_node.linked_dataset
            and rr_node.forward is not None):
        fwd_rr = nodes.get(rr_node.forward)
        if fwd_rr is not None:
            result_r = leftof(leftof(fwd_rr.view))
            right_view = result_r if result_r in nodes else None
        else:
            right_view = None
    else:
        right_view = None

    # ── left: leftof(leftof(v)) → forward → rightof(rightof(result)) ─────────
    ll = leftof(leftof(v))
    ll_node = nodes.get(ll)
    if (ll_node is not None
            and not ll_node.linked_dataset
            and ll_node.forward is not None):
        fwd_ll = nodes.get(ll_node.forward)
        if fwd_ll is not None:
            result_l = rightof(rightof(fwd_ll.view))
            left_view = result_l if result_l in nodes else None
        else:
            left_view = None
    else:
        left_view = None

    return {
        "forward":    fwd_view,
        "back":       back_view,
        "left":       left_view,
        "right":      right_view,
        "turn_left":  leftof(v),
        "turn_right": rightof(v),
    }


# ---------------------------------------------------------------------------
# Plan-node graph builder (mirrors /api/plan_nodes logic in server.py)
# ---------------------------------------------------------------------------


def _build_plan_nodes(ds) -> dict:
    """Return {plan_number_str: {plan_frame, positions, edges}} for all plans."""
    plan_numbers: set[int] = set()
    for node in ds.nodes.values():
        if node.plan is not None:
            plan_numbers.add(node.plan.plan_number)

    result: dict[str, dict] = {}
    for pn in sorted(plan_numbers):
        pos_index: dict[tuple[int, int], int] = {}
        positions: list[dict] = []
        for v, node in sorted(ds.nodes.items()):
            if node.plan is not None and node.plan.plan_number == pn:
                key = (node.plan.x, node.plan.y)
                if key not in pos_index:
                    pos_index[key] = len(positions)
                    positions.append({
                        "view": v,
                        "x": node.plan.x,
                        "y": node.plan.y,
                        "has_details": len(node.details) > 0,
                    })

        edge_set: set[tuple[int, int]] = set()
        edges: list[dict] = []
        for node in ds.nodes.values():
            if (node.plan is None
                    or node.plan.plan_number != pn
                    or node.forward is None
                    or node.linked_dataset):
                continue
            fwd = ds.nodes.get(node.forward)
            if fwd is None or fwd.plan is None or fwd.plan.plan_number != pn:
                continue
            src = pos_index.get((node.plan.x, node.plan.y))
            dst = pos_index.get((fwd.plan.x, fwd.plan.y))
            if src is None or dst is None or src == dst:
                continue
            key = (min(src, dst), max(src, dst))
            if key not in edge_set:
                edge_set.add(key)
                edges.append({"from": src, "to": dst})

        result[str(pn)] = {
            "plan_frame": ds.get_plan_frame(pn),
            "positions": positions,
            "edges": edges,
        }

    return result


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Build a static Domesday walk site")
    p.add_argument("--disc", default="NationalA", help="Disc name (default: NationalA)")
    p.add_argument("--walk", default="brecon",    help="Walk name (default: brecon)")
    p.add_argument("--gallery", type=Path, default=None,
                   help="Path to GALLERY file (auto-detected under data/)")
    p.add_argument("--jpgimg", type=Path, default=None,
                   help="Path to jpgimg directory (auto-detected under data/)")
    p.add_argument("--output", type=Path, default=None,
                   help="Output directory (default: dist/{disc}/{walk}/)")
    args = p.parse_args()

    root = Path(__file__).parent.parent
    gallery_path = args.gallery or root / "data" / args.disc / "VFS" / "GALLERY"
    jpgimg_path  = args.jpgimg  or root / "data" / args.disc / "jpgimg"
    out_dir      = args.output  or root / "dist" / args.disc / args.walk

    if not gallery_path.exists():
        raise SystemExit(f"GALLERY not found: {gallery_path}")
    if not jpgimg_path.exists():
        raise SystemExit(f"jpgimg not found: {jpgimg_path}")

    # Discover the walk's byte offset within the GALLERY file
    walks = discover_walks(gallery_path)
    walk_meta = next((w for w in walks if w["name"] == args.walk.lower()), None)
    if walk_meta is None:
        available = [w["name"] for w in walks]
        raise SystemExit(f"Walk '{args.walk}' not found. Available: {available}")

    offset = walk_meta["gallery_offset"]
    print(f"Walk '{args.walk}' at gallery offset {offset}")

    # Load dataset
    gallery_data = gallery_path.read_bytes()
    ds_data = gallery_data[offset:]          # slice for parse_closeup_frames
    ds = load_gallery_subdataset(gallery_path, offset)
    print(f"  {len(ds.nodes)} nodes, initial_view={ds.initial_view}, "
          f"base_view={ds.base_view}, base_plan={ds.base_plan}")

    # Build node list with pre-computed nav + embedded detail frames
    all_frames: set[int] = set()
    nodes_json: dict[str, dict] = {}

    for v, node in sorted(ds.nodes.items()):
        d = asdict(node)
        d["plan_frame"] = ds.get_plan_frame(node.plan.plan_number) if node.plan else None
        d["nav"] = _build_nav(v, ds.nodes)

        # Resolve detail closeup frames and embed in each icon
        if node.details:
            new_details = []
            for icon in node.details:
                entry = asdict(icon)
                try:
                    frames = parse_closeup_frames(
                        ds_data, ds.dtable_byte, icon.item_offset, ds.base_view
                    )
                except Exception:
                    frames = []
                entry["frames"] = frames
                all_frames.update(frames)
                new_details.append(entry)
            d["details"] = new_details

        # Collect frames to copy
        all_frames.add(node.frame)
        if d["plan_frame"] is not None:
            all_frames.add(d["plan_frame"])

        # Strip fields not needed in the static site
        d.pop("linked_dataset", None)
        d.pop("link_target", None)

        nodes_json[str(v)] = d

    # Build plan-node graph for map modal
    plan_nodes = _build_plan_nodes(ds)

    # Assemble JSON payload
    payload = {
        "name":         args.walk.lower(),
        "initial_view": ds.initial_view,
        "base_view":    ds.base_view,
        "base_plan":    ds.base_plan,
        "plan_nodes":   plan_nodes,
        "nodes":        nodes_json,
    }

    # Write output
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "walk.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"  Wrote {json_path} ({json_path.stat().st_size:,} bytes)")

    # Copy JPEG frames
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    copied = missing = 0
    for frame in sorted(all_frames):
        bucket = f"{frame // 1000:02d}"
        src = jpgimg_path / bucket / f"{frame}.jpg"
        if not src.exists():
            missing += 1
            continue
        dst_bucket = frames_dir / bucket
        dst_bucket.mkdir(exist_ok=True)
        shutil.copy2(src, dst_bucket / f"{frame}.jpg")
        copied += 1
    print(f"  Copied {copied} frames ({missing} missing) → {frames_dir}/")

    # Copy static HTML template
    static_src = root / "src" / "domesday" / "static" / "index-static.html"
    if static_src.exists():
        shutil.copy2(static_src, out_dir / "index.html")
        print(f"  Copied index.html → {out_dir}/")
    else:
        print(f"  Warning: {static_src} not found — no index.html written")

    print(f"\nDone! Serve with:")
    print(f"  cd {out_dir} && python -m http.server 8080")
    print(f"  → http://localhost:8080/")


if __name__ == "__main__":
    main()

"""FastAPI server for BBC Domesday Walk/Gallery navigation.

Usage:
    python -m domesday.server --gallery data/national/NationalA/GALLERY \\
                               --jpgimg  data/national/jpgimg

Endpoints:
    GET /                          → index.html
    GET /api/view/{view_id}        → Node JSON for a specific view
    GET /api/navigate/{view}/{dir} → Node JSON after moving one step in direction dir (0-7)
    GET /frame/{frame_number}      → streams JPEG from jpgimg/
    GET /api/dataset               → full node graph as JSON
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from domesday.models import Node, WalkDataset
from domesday.parser import (
    decode_names_address, leftof, load_gallery, load_gallery_subdataset, load_walk,
    parse_closeup_frames, parse_essay, parse_gallery_item, parse_names_record, parse_photo_set,
    probe_data_type, rightof,
)
from domesday.export import discover_walks
from domesday.catalogue import extract_catalogue
from domesday.nm_reader import (
    compute_nm_stats, grid_to_png, parse_nm_classification,
    read_nm_text_addresses, render_nm_region,
)

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

_nm_stats_cache: dict[tuple, dict] = {}

_app_state: dict[str, Any] = {
    "gallery": None,
    "jpgimg": None,
    "gallery_path": None,
    "data1_path": None,
    "data2_path": None,
    "names_path": None,     # Path to national NAMES file
    "adf_path": None,       # Path to nationalA.adf disc image (for NM rendering)
    "bbc_h_offset":  8.65,  # % from JPEG left where BBC x=0 appears (PAL timing)
    "bbc_h_scale":  76.9,   # % of JPEG width covered by BBC x range (0-1279)
    "bbc_v_offset":  5.56,  # % from JPEG top where BBC y=1023 appears (PAL timing)
    "bbc_v_scale":  88.88,  # % of JPEG height covered by BBC y range (0-1023)
}

app = FastAPI(title="BBC Domesday Walk Navigator")

_STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helper: serialise a Node to a JSON-safe dict
# ---------------------------------------------------------------------------


def _node_to_dict(node: Node, dataset: WalkDataset) -> dict[str, Any]:
    d = asdict(node)
    # Resolve plan frame number if plan is present
    if node.plan is not None:
        plan_frame = dataset.get_plan_frame(node.plan.plan_number)
        d["plan_frame"] = plan_frame
    else:
        d["plan_frame"] = None
    # Add navigability flags for all 8 compass slots (0-7)
    # In BCPL, views within a group are 1-indexed; here we expose 0-indexed directions.
    d["navigable"] = _navigability(node, dataset)
    return d


def _navigability(node: Node, dataset: WalkDataset) -> dict[str, bool]:
    """Return a dict of direction→can_navigate for all 8 compass slots."""
    # Navigation is only tested for forward; turning is always available.
    # "forward" is direction 0 (North) conceptually — the UI exposes it as the centre slot.
    # We expose: can the user move forward from THIS view?
    result = {
        "forward": node.forward is not None,
        "back": False,  # computed below
    }
    # Back = can the opposite view move forward?
    opp_view = _opposite(node.view)
    if opp_view in dataset.nodes:
        opp_node = dataset.nodes[opp_view]
        result["back"] = opp_node.forward is not None
    return result


def _opposite(view: int) -> int:
    """Turn 180 degrees (4 × leftof)."""
    v = view
    for _ in range(4):
        v = leftof(v)
    return v


def _get_dataset() -> WalkDataset:
    ds = _app_state["gallery"]
    if ds is None:
        raise HTTPException(status_code=503, detail="Gallery not loaded")
    return ds


def _load_dataset_by_offset(offset: int) -> WalkDataset:
    """Return the gallery dataset (offset=0) or a sub-dataset at the given byte offset."""
    if offset == 0:
        return _get_dataset()
    gallery_path: Path | None = _app_state.get("gallery_path")
    if gallery_path is None:
        raise HTTPException(status_code=503, detail="Gallery path not configured")
    return load_gallery_subdataset(gallery_path, offset)


def _resolve_node(dataset: WalkDataset, view: int) -> Node:
    node = dataset.nodes.get(view)
    if node is None:
        raise HTTPException(status_code=404, detail=f"View {view} not found")
    return node


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    return RedirectResponse("/domesday-navigator/NationalA/walk/Gallery/view/1/")


@app.get("/api/config")
async def get_config():
    """Return client-side rendering configuration (PAL display geometry)."""
    return JSONResponse({
        "bbc_h_offset": _app_state["bbc_h_offset"],
        "bbc_h_scale":  _app_state["bbc_h_scale"],
        "bbc_v_offset": _app_state["bbc_v_offset"],
        "bbc_v_scale":  _app_state["bbc_v_scale"],
    })


@app.get("/api/view/{view_id}")
async def get_view(view_id: int, dataset: int = 0):
    ds = _load_dataset_by_offset(dataset)
    node = _resolve_node(ds, view_id)
    result = _node_to_dict(node, ds)
    result["dataset_offset"] = dataset
    return JSONResponse(result)


@app.get("/api/initial")
async def get_initial_view():
    """Return the initial view node."""
    ds = _get_dataset()
    node = _resolve_node(ds, ds.initial_view)
    result = _node_to_dict(node, ds)
    result["dataset_offset"] = 0
    return JSONResponse(result)


@app.get("/api/navigate/{view_id}/{direction}")
async def navigate(view_id: int, direction: str, dataset: int = 0):
    """Navigate from view_id in the given direction.

    Query parameters:
        dataset: byte offset of the current dataset within the gallery file (0 = main gallery)

    direction values:
        forward, back, left, right, turn_left, turn_right
    """
    ds = _load_dataset_by_offset(dataset)
    node = _resolve_node(ds, view_id)

    # If the current node is a gateway into a linked sub-dataset, handle it before
    # the normal direction handler — which would wrongly look up node.forward in ds.
    if direction == "forward" and node.linked_dataset and node.link_target is not None:
        linked_ds = _load_linked(node)
        if linked_ds is None:
            raise HTTPException(status_code=404, detail="Could not load linked dataset")
        entry_node = linked_ds.nodes.get(node.forward)
        if entry_node is None:
            raise HTTPException(status_code=404, detail=f"View {node.forward} not found in linked dataset")
        result = _node_to_dict(entry_node, linked_ds)
        result["dataset_offset"] = node.link_target.byte_offset
        return JSONResponse(result)

    direction_map = {
        "forward": _go_forward,
        "back": _go_back,
        "right": _go_right,
        "left": _go_left,
        "turn_right": lambda n, d: d.nodes.get(rightof(n.view)),
        "turn_left": lambda n, d: d.nodes.get(leftof(n.view)),
    }

    handler = direction_map.get(direction)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown direction '{direction}'")

    next_node = handler(node, ds)
    if next_node is None:
        raise HTTPException(status_code=404, detail="No move possible in this direction")

    result = _node_to_dict(next_node, ds)
    result["dataset_offset"] = dataset
    return JSONResponse(result)


def _go_forward(node: Node, dataset: WalkDataset) -> Node | None:
    if node.forward is None:
        return None
    return dataset.nodes.get(node.forward)


def _go_back(node: Node, dataset: WalkDataset) -> Node | None:
    opp = _opposite(node.view)
    opp_node = dataset.nodes.get(opp)
    if opp_node is None or opp_node.forward is None:
        return None
    return dataset.nodes.get(opp_node.forward)


def _go_right(node: Node, dataset: WalkDataset) -> Node | None:
    # "Going right" = turn right 90°, step forward, turn left 90°
    rr = rightof(rightof(node.view))
    rr_node = dataset.nodes.get(rr)
    if rr_node is None or rr_node.forward is None:
        return None
    fwd = dataset.nodes.get(rr_node.forward)
    if fwd is None:
        return None
    return dataset.nodes.get(leftof(leftof(fwd.view)))


def _go_left(node: Node, dataset: WalkDataset) -> Node | None:
    ll = leftof(leftof(node.view))
    ll_node = dataset.nodes.get(ll)
    if ll_node is None or ll_node.forward is None:
        return None
    fwd = dataset.nodes.get(ll_node.forward)
    if fwd is None:
        return None
    return dataset.nodes.get(rightof(rightof(fwd.view)))


def _load_linked(node: Node) -> WalkDataset | None:
    """Load a linked dataset from the appropriate file."""
    if node.link_target is None:
        return None
    lt = node.link_target
    gallery_path: Path | None = _app_state["gallery_path"]
    data1_path: Path | None = _app_state["data1_path"]
    data2_path: Path | None = _app_state["data2_path"]

    # In gallery mode (syslev=1), links always refer to the GALLERY file itself
    if _app_state["gallery"] and _app_state["gallery"].syslev == 1 and gallery_path:
        return load_gallery_subdataset(gallery_path, lt.byte_offset)
    # Otherwise use DATA1 or DATA2
    if lt.use_data2 and data2_path:
        return load_walk(data2_path, lt.byte_offset)
    if not lt.use_data2 and data1_path:
        return load_walk(data1_path, lt.byte_offset)
    return None


@app.get("/frame/{frame_number}")
async def get_frame(frame_number: int):
    jpgimg: Path | None = _app_state["jpgimg"]
    if jpgimg is None:
        raise HTTPException(status_code=503, detail="jpgimg path not configured")
    ds = _get_dataset()
    frame_path = ds.get_frame_path(frame_number, jpgimg)
    if not frame_path.exists():
        raise HTTPException(status_code=404, detail=f"Frame {frame_number} not found at {frame_path}")
    return FileResponse(str(frame_path), media_type="image/jpeg")


@app.get("/domesday-navigator/NationalA/frame/{bucket}/{frame_number}/frame.jpg")
async def get_frame_v2(bucket: str, frame_number: int):
    jpgimg: Path | None = _app_state["jpgimg"]
    if jpgimg is None:
        raise HTTPException(status_code=503, detail="jpgimg path not configured")
    ds = _get_dataset()
    frame_path = ds.get_frame_path(frame_number, jpgimg)
    if not frame_path.exists():
        raise HTTPException(status_code=404, detail=f"Frame {frame_number} not found at {frame_path}")
    return FileResponse(str(frame_path), media_type="image/jpeg")


@app.get("/api/walks")
async def get_walks():
    """Return metadata for all walk sub-datasets embedded in the gallery."""
    gallery_path: Path | None = _app_state.get("gallery_path")
    if gallery_path is None:
        raise HTTPException(status_code=503, detail="Gallery path not configured")
    walks = discover_walks(gallery_path)
    return JSONResponse(walks)


@app.get("/api/detail")
async def get_detail(dataset: int = 0, item_offset: int = 0):
    """Return detail info for a detail icon (gallery NAMES lookup or walk closeup chain)."""
    ds = _load_dataset_by_offset(dataset)
    gallery_path: Path | None = _app_state.get("gallery_path")
    if gallery_path is None:
        raise HTTPException(status_code=503, detail="Gallery path not configured")
    data = gallery_path.read_bytes()

    if ds.syslev == 1:
        # Gallery mode: resolve via NAMES file
        names_path: Path | None = _app_state.get("names_path")
        if names_path is None:
            raise HTTPException(status_code=503, detail="NAMES file not configured or not found")
        record_index = parse_gallery_item(data[dataset:], ds.dtable_byte, item_offset)
        rec = parse_names_record(names_path.read_bytes(), record_index)

        if rec['type'] == 8:  # Photo — decode address, read photo set from DATA1/DATA2
            is_data2, file_offset = decode_names_address(rec['address'])
            data_path: Path | None = _app_state.get("data2_path" if is_data2 else "data1_path")
            if data_path is None:
                raise HTTPException(status_code=503, detail="DATA1/DATA2 not configured")
            photo = parse_photo_set(data_path, file_offset)
            return JSONResponse({
                'frames': photo['frames'],
                'captions': photo['captions'],
                'descriptions': photo['descriptions'],
                'pages': [],
                'page_titles': [],
                'title': rec['title'],
                'type': rec['type'],
            })
        elif rec['type'] in (6, 7):  # Essay / Picture Essay
            is_data2, file_offset = decode_names_address(rec['address'])
            data_path = _app_state.get("data2_path" if is_data2 else "data1_path")
            if data_path is None:
                raise HTTPException(status_code=503, detail="DATA1/DATA2 not configured")
            essay = parse_essay(data_path, file_offset)
            return JSONResponse({
                'frames': [],
                'captions': [],
                'descriptions': [],
                'pages': essay['pages'],
                'page_titles': essay['titles'],
                'figures': essay['figures'],
                'title': rec['title'],
                'type': rec['type'],
            })
        else:
            return JSONResponse({
                'frames': [],
                'captions': [],
                'descriptions': [],
                'pages': [],
                'page_titles': [],
                'title': rec['title'],
                'type': rec['type'],
            })
    else:
        # Walk mode: closeup chain (existing behaviour, no captions)
        frames = parse_closeup_frames(data[dataset:], ds.dtable_byte, item_offset, ds.base_view)
        return JSONResponse({
            'frames': frames,
            'captions': [],
            'descriptions': [],
            'pages': [],
            'page_titles': [],
            'title': '',
            'type': -1,
        })


@app.get("/api/detail_titles")
async def get_detail_titles(dataset: int = 0, view: int = 0):
    """Return {item_offset: {title, type}} for all detail icons in a view.

    Gallery mode: resolves NAMES title. Walk mode: returns frame count label.
    """
    ds = _load_dataset_by_offset(dataset)
    node = ds.nodes.get(view)
    if node is None or not node.details:
        return JSONResponse({})
    gallery_path: Path | None = _app_state.get("gallery_path")
    if gallery_path is None:
        return JSONResponse({})
    data = gallery_path.read_bytes()
    result = {}

    if ds.syslev == 1:
        names_path: Path | None = _app_state.get("names_path")
        if names_path is None:
            return JSONResponse({})
        names_data = names_path.read_bytes()
        for icon in node.details:
            try:
                record_index = parse_gallery_item(data[dataset:], ds.dtable_byte, icon.item_offset)
                rec = parse_names_record(names_data, record_index)
                result[str(icon.item_offset)] = {'title': rec['title'], 'type': rec['type']}
            except Exception:
                pass
    else:
        for icon in node.details:
            try:
                frames = parse_closeup_frames(
                    data[dataset:], ds.dtable_byte, icon.item_offset, ds.base_view
                )
                n = len(frames)
                result[str(icon.item_offset)] = {
                    'title': f'Close-up ({n} frame{"s" if n != 1 else ""})',
                    'type': -1,
                }
            except Exception:
                pass

    return JSONResponse(result)


@app.get("/api/figure_photos")
async def get_figure_photos(address: int):
    """Decode a raw 32-bit figure address and return the photo set or essay at that location."""
    is_data2, file_offset = decode_names_address(address)
    data_path: Path | None = _app_state.get("data2_path" if is_data2 else "data1_path")
    if data_path is None:
        raise HTTPException(status_code=503, detail="DATA file not configured")
    content_type = probe_data_type(data_path, file_offset)
    if content_type == 'essay':
        essay = parse_essay(data_path, file_offset)
        return JSONResponse({
            'frames': [], 'captions': [], 'descriptions': [],
            'pages': essay['pages'],
            'page_titles': essay['titles'],
            'figures': essay['figures'],
            'title': 'Figure', 'type': 6,
        })
    else:
        photo = parse_photo_set(data_path, file_offset)
        return JSONResponse({
            'frames': photo['frames'],
            'captions': photo['captions'],
            'descriptions': photo['descriptions'],
            'pages': [], 'page_titles': [], 'figures': [],
            'title': 'Figure', 'type': 8,
        })


@app.get("/api/plan_nodes")
async def get_plan_nodes(dataset: int = 0, plan_number: int = 0):
    """Return all unique plan positions and their forward-link edges for a given dataset and plan number."""
    ds = _load_dataset_by_offset(dataset)

    # Build deduplicated position list
    pos_index: dict[tuple[int, int], int] = {}  # (x, y) → index in positions list
    positions: list[dict] = []
    for v, node in sorted(ds.nodes.items()):
        if node.plan is not None and node.plan.plan_number == plan_number:
            key = (node.plan.x, node.plan.y)
            if key not in pos_index:
                pos_index[key] = len(positions)
                positions.append({
                    "view": v,
                    "x": node.plan.x,
                    "y": node.plan.y,
                    "has_details": len(node.details) > 0,
                })

    # Build edges between unique positions (forward links)
    edge_set: set[tuple[int, int]] = set()
    edges: list[dict] = []
    for node in ds.nodes.values():
        if node.plan is None or node.plan.plan_number != plan_number or node.forward is None:
            continue
        if node.linked_dataset:  # forward points into a different dataset — skip
            continue
        fwd = ds.nodes.get(node.forward)
        if fwd is None or fwd.plan is None or fwd.plan.plan_number != plan_number:
            continue
        src = pos_index.get((node.plan.x, node.plan.y))
        dst = pos_index.get((fwd.plan.x, fwd.plan.y))
        if src is None or dst is None or src == dst:
            continue
        key = (min(src, dst), max(src, dst))
        if key not in edge_set:
            edge_set.add(key)
            edges.append({"from": src, "to": dst})

    return JSONResponse({
        "plan_frame": ds.get_plan_frame(plan_number),
        "positions": positions,
        "edges": edges,
    })


@app.get("/domesday-navigator/NationalA/GridMap")
@app.get("/domesday-navigator/NationalA/GridMap/{path:path}")
async def nm_page(path: str = ""):
    return FileResponse(str(_STATIC_DIR / "nm.html"))


@app.get("/domesday-navigator/{path:path}")
async def spa_catchall(path: str):
    if path.endswith("/frame.jpg"):
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/api/dataset")
async def get_dataset():
    ds = _get_dataset()
    nodes_json = {str(k): _node_to_dict(v, ds) for k, v in ds.nodes.items()}
    return JSONResponse(
        {
            "source_file": str(ds.source_file),
            "syslev": ds.syslev,
            "base_view": ds.base_view,
            "base_plan": ds.base_plan,
            "initial_view": ds.initial_view,
            "node_count": len(ds.nodes),
            "nodes": nodes_json,
        }
    )


# ---------------------------------------------------------------------------
# NM map endpoints
# ---------------------------------------------------------------------------


def _get_data_dir() -> Path:
    """Derive the NationalA data directory from gallery_path."""
    gallery_path: Path | None = _app_state.get("gallery_path")
    if gallery_path is None:
        raise HTTPException(status_code=503, detail="Gallery path not configured")
    # gallery_path = .../NationalA/VFS/GALLERY  →  parent.parent = .../NationalA
    return gallery_path.parent.parent


@app.get("/api/nm/catalogue")
async def nm_catalogue():
    """List NM datasets (types 1, 2, 3) from the NAMES/HIERARCHY catalogue."""
    data_dir = _get_data_dir()
    entries = extract_catalogue(data_dir)
    nm = [e for e in entries if e.item_type in (1, 2, 3)]
    return JSONResponse([{
        "record_no": e.record_no,
        "name": e.name,
        "item_type": e.item_type,
        "type_name": e.type_name,
        "address": e.address,
        "path": e.path,
    } for e in nm])


@app.get("/api/nm/render.png")
async def nm_render(
    record_no: int,
    e_min: int,
    n_min: int,
    e_max: int,
    n_max: int,
    mode: str = "bands",
    scale: int = 1,
    legend: bool = False,
):
    """Render an NM grid-map dataset as a PNG for the given OS grid bbox (km).

    mode:   "bands" (5-band BBC choropleth) or "distinct" (one colour per value)
    scale:  pixel multiplier 1–8 (each 1 km² rendered as scale×scale pixels)
    legend: if true, append a colour-key strip to the right of the image
    """
    adf_path: Path | None = _app_state.get("adf_path")
    if adf_path is None:
        raise HTTPException(status_code=503, detail="ADF not configured (use --adf)")
    names_path: Path | None = _app_state.get("names_path")
    if names_path is None:
        raise HTTPException(status_code=503, detail="NAMES file not configured")
    VALID_MODES = ("bands", "distinct", "viridis", "greyscale")
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {VALID_MODES}")
    rec = parse_names_record(names_path.read_bytes(), record_no)
    sector_addr = rec["address"]

    # Look up title and hierarchy path from the catalogue
    dataset_title = rec.get("title", "")
    dataset_path: list[str] = []
    try:
        data_dir = _get_data_dir()
        entries = extract_catalogue(data_dir)
        cat_entry = next((e for e in entries if e.record_no == record_no), None)
        if cat_entry:
            dataset_title = cat_entry.name
            dataset_path = list(cat_entry.path)
    except Exception:
        pass

    try:
        grid, meta = render_nm_region(adf_path, sector_addr, e_min, n_min, e_max, n_max)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Auto-fetch classification labels for distinct/viridis/greyscale legend
    classification: dict[int, str] | None = None
    if legend and mode in ("distinct", "viridis", "greyscale"):
        try:
            data1_path = _app_state.get("data1_path")
            data2_path = _app_state.get("data2_path")
            addrs = read_nm_text_addresses(adf_path, sector_addr)
            desc_addr = addrs["descriptive"]
            if desc_addr not in (0, 0xFFFFFFFF) and (data1_path or data2_path):
                is_data2, file_offset = decode_names_address(desc_addr)
                data_path = data2_path if is_data2 else data1_path
                if data_path:
                    essay = parse_essay(data_path, file_offset)
                    classification = parse_nm_classification(essay["pages"])
        except Exception:
            pass

    info_seg = f"#{record_no}  ·  E:{e_min}–{e_max} km  N:{n_min}–{n_max} km"
    png_bytes = grid_to_png(
        grid, meta,
        mode=mode, scale=scale, legend=legend,
        title=dataset_title, path=list(dataset_path),
        info=info_seg,
        classification=classification,
    )
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")


@app.get("/api/nm/text")
async def nm_text(record_no: int):
    """Return the descriptive text and value classification for an NM dataset."""
    adf_path: Path | None = _app_state.get("adf_path")
    names_path: Path | None = _app_state.get("names_path")
    if adf_path is None or names_path is None:
        raise HTTPException(status_code=503, detail="ADF/NAMES not configured")
    rec = parse_names_record(names_path.read_bytes(), record_no)
    addrs = read_nm_text_addresses(adf_path, rec["address"])
    desc_addr = addrs["descriptive"]
    if desc_addr in (0, 0xFFFFFFFF):
        return JSONResponse({"pages": [], "classification": {}})
    is_data2, file_offset = decode_names_address(desc_addr)
    data_path = _app_state.get("data2_path" if is_data2 else "data1_path")
    if data_path is None:
        raise HTTPException(status_code=503, detail="DATA1/DATA2 not configured")
    essay = parse_essay(data_path, file_offset)
    classification = parse_nm_classification(essay["pages"])
    return JSONResponse({
        "title": essay["titles"][0] if essay["titles"] else "",
        "pages": essay["pages"],
        "classification": {str(k): v for k, v in sorted(classification.items())},
    })


@app.get("/api/nm/stats/{record_no}")
async def nm_stats(record_no: int):
    """Compute and cache full-dataset statistics for an NM grid-mappable dataset.

    First call scans the entire ADF (may take several seconds for large datasets).
    Subsequent calls return the cached result instantly.
    """
    adf_path: Path | None = _app_state.get("adf_path")
    names_path: Path | None = _app_state.get("names_path")
    if adf_path is None or names_path is None:
        raise HTTPException(status_code=503, detail="ADF/NAMES not configured")

    cache_key = (str(adf_path), record_no)
    if cache_key in _nm_stats_cache:
        return JSONResponse(_nm_stats_cache[cache_key])

    rec = parse_names_record(names_path.read_bytes(), record_no)
    sector_addr = rec["address"]

    try:
        stats = await asyncio.get_event_loop().run_in_executor(
            None, compute_nm_stats, adf_path, sector_addr
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = {"record_no": record_no, **stats}
    _nm_stats_cache[cache_key] = result
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="BBC Domesday Walk Navigator")
    p.add_argument(
        "--gallery",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_GALLERY", "data/NationalA/VFS/GALLERY")),
        help="Path to the GALLERY data file",
    )
    p.add_argument(
        "--jpgimg",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_JPGIMG", "data/NationalA/jpgimg")),
        help="Path to the jpgimg directory containing JPEG frames",
    )
    p.add_argument(
        "--data1",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_DATA1", "data/NationalA/VFS/DATA1")),
        help="Path to DATA1 file (for linked walk datasets)",
    )
    p.add_argument(
        "--data2",
        type=Path,
        default=Path(os.environ.get("DOMESDAY_DATA2", "data/NationalA/VFS/DATA2")),
        help="Path to DATA2 file (for linked walk datasets)",
    )
    p.add_argument(
        '--names',
        type=Path,
        default=Path(os.environ.get('DOMESDAY_NAMES', 'data/NationalA/VFS/NAMES')),
        help='Path to national NAMES file for gallery item lookup',
    )
    p.add_argument(
        '--adf',
        type=Path,
        default=Path(os.environ.get('DOMESDAY_ADF', 'data/nationalA.adf')),
        help='Path to ADF disc image for NM map rendering (default: data/nationalA.adf)',
    )
    p.add_argument(
        "--bbc-h-offset",
        type=float,
        default=float(os.environ.get("DOMESDAY_BBC_H_OFFSET", "12.5")),
        help="Horizontal offset (%%) — BBC x=0 position in JPEG frame (default: 8.65)",
    )
    p.add_argument(
        "--bbc-h-scale",
        type=float,
        default=float(os.environ.get("DOMESDAY_BBC_H_SCALE", "76.9")),
        help="Horizontal scale (%%) — BBC x range width in JPEG frame (default: 76.9)",
    )
    p.add_argument(
        "--bbc-v-offset",
        type=float,
        default=float(os.environ.get("DOMESDAY_BBC_V_OFFSET", "5.56")),
        help="Vertical offset (%%) — BBC y=1023 position in JPEG frame (default: 5.56)",
    )
    p.add_argument(
        "--bbc-v-scale",
        type=float,
        default=float(os.environ.get("DOMESDAY_BBC_V_SCALE", "88.88")),
        help="Vertical scale (%%) — BBC y range height in JPEG frame (default: 88.88)",
    )
    p.add_argument("--host", default="0.0.0.0", help="Bind host")
    p.add_argument("--port", type=int, default=8000, help="Bind port")
    return p


def main() -> None:
    import uvicorn

    args = _build_arg_parser().parse_args()

    if not args.gallery.exists():
        raise SystemExit(f"GALLERY file not found: {args.gallery}")
    if not args.jpgimg.exists():
        raise SystemExit(f"jpgimg directory not found: {args.jpgimg}")

    print(f"Loading gallery from {args.gallery}…")
    gallery_ds = load_gallery(args.gallery)
    print(f"  {len(gallery_ds.nodes)} views loaded, initial_view={gallery_ds.initial_view}, syslev={gallery_ds.syslev}")

    _app_state["gallery"] = gallery_ds
    _app_state["jpgimg"] = args.jpgimg
    _app_state["gallery_path"] = args.gallery
    _app_state["data1_path"] = args.data1 if args.data1.exists() else None
    _app_state["data2_path"] = args.data2 if args.data2.exists() else None
    if args.names.exists():
        _app_state["names_path"] = args.names
        print(f"  NAMES file: {args.names}")
    else:
        _app_state["names_path"] = None
        print(f"  Warning: NAMES file not found at {args.names} — gallery detail lookup disabled")
    if args.adf.exists():
        _app_state["adf_path"] = args.adf
        print(f"  ADF file: {args.adf}")
    else:
        _app_state["adf_path"] = None
        print(f"  Warning: ADF file not found at {args.adf} — NM map rendering disabled")
    _app_state["bbc_h_offset"] = args.bbc_h_offset
    _app_state["bbc_h_scale"]  = args.bbc_h_scale
    _app_state["bbc_v_offset"] = args.bbc_v_offset
    _app_state["bbc_v_scale"]  = args.bbc_v_scale
    print(f"  PAL geometry: h_offset={args.bbc_h_offset}%  h_scale={args.bbc_h_scale}%  v_offset={args.bbc_v_offset}%  v_scale={args.bbc_v_scale}%")

    print(f"Server starting at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

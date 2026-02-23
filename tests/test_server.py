"""Server-level tests for BBC Domesday Walk/Gallery FastAPI application.

These tests patch _app_state directly to avoid needing a running server.
Synthetic data tests require no data files; real-data tests are skipped
when the GALLERY and NAMES files are absent.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from domesday.parser import parse_dataset
from domesday.server import _app_state, app

# ---------------------------------------------------------------------------
# Paths and skip decorators (real-data tests only)
# ---------------------------------------------------------------------------

REPO_ROOT    = Path(__file__).parent.parent
GALLERY_PATH = REPO_ROOT / "data" / "NationalA" / "VFS" / "GALLERY"
NAMES_PATH   = REPO_ROOT / "data" / "NationalA" / "VFS" / "NAMES"


def require_gallery(fn):
    return pytest.mark.skipif(
        not GALLERY_PATH.exists(),
        reason=f"GALLERY not found at {GALLERY_PATH}",
    )(fn)


def require_names(fn):
    return pytest.mark.skipif(
        not NAMES_PATH.exists(),
        reason=f"NAMES not found at {NAMES_PATH}",
    )(fn)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_app_state():
    """Save and restore _app_state around each test."""
    saved = dict(_app_state)
    yield
    _app_state.clear()
    _app_state.update(saved)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_walk_dataset(tmp_path: Path, frame_deltas: list[int]):
    """Build a minimal walk dataset with one view containing one detail icon.

    View 1 has a single detail icon at item_offset=3, pointing to a closeup
    chain with the given frame_deltas relative to base_view=100.

    Returns (gallery_file_path, WalkDataset).
    """
    buf = bytearray(256)

    def w16(offset, val):
        struct.pack_into("<h", buf, offset, val)

    def wu16(offset, val):
        struct.pack_into("<H", buf, offset, val)

    ltable_offset = 60
    ctable_offset = 120
    ptable_offset = 128   # 2 ctable entries × 4 bytes = 8 bytes
    dtable_offset = 132   # 1 ptable entry × 4 bytes = 4 bytes

    # Header
    w16(28, ltable_offset)
    w16(32, ctable_offset)
    w16(36, ptable_offset)
    w16(40, dtable_offset)
    w16(50, 62)            # detail word-count: (256 - 132) // 2
    wu16(54, 101)          # base_view + 1 = 101 → base_view = 100
    wu16(56, 50)           # base_plan
    w16(58, 0)             # syslev = 0 (walk mode, not gallery)

    # ctable: slot 0 = initial_view, slot 1 = view 1
    w16(ctable_offset,     1)    # initial_view = 1
    w16(ctable_offset + 2, 0)    # slot 0 detail (unused)
    w16(ctable_offset + 4, 0)    # view 1: next_view = 0 (dead-end)
    w16(ctable_offset + 6, 0)    # view 1: detail_offset = 0

    # Detail icon at dtable_offset + 0:
    #   x_raw = -100 (negative → list terminates after this entry, x = 100)
    w16(dtable_offset + 0, -100)  # x_raw
    w16(dtable_offset + 2,  200)  # y
    w16(dtable_offset + 4,    3)  # item_offset = 3

    # Closeup chain at dtable_offset + 3*2 = dtable_offset + 6
    chain_pos = dtable_offset + 6
    wu16(chain_pos, len(frame_deltas))
    for i, delta in enumerate(frame_deltas):
        w16(chain_pos + 2 + i * 2, delta)

    data = bytes(buf)
    gallery_file = tmp_path / "GALLERY"
    gallery_file.write_bytes(data)
    ds = parse_dataset(data, source_file=gallery_file, byte_offset=0)
    return gallery_file, ds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detail_titles_walk_1frame(tmp_path, reset_app_state):
    """Walk-mode detail_titles for a 1-frame closeup returns the singular label."""
    gallery_file, ds = _make_synthetic_walk_dataset(tmp_path, [5])
    _app_state["gallery"] = ds
    _app_state["gallery_path"] = gallery_file

    client = TestClient(app)
    response = client.get("/api/detail_titles?dataset=0&view=1")
    assert response.status_code == 200
    result = response.json()
    assert "3" in result, f"Expected key '3' (item_offset) in response: {result}"
    assert result["3"]["title"] == "Close-up (1 frame)"
    assert result["3"]["type"] == -1


def test_detail_titles_walk_3frames(tmp_path, reset_app_state):
    """Walk-mode detail_titles for a 3-frame closeup returns the plural label."""
    gallery_file, ds = _make_synthetic_walk_dataset(tmp_path, [1, 2, -1])
    _app_state["gallery"] = ds
    _app_state["gallery_path"] = gallery_file

    client = TestClient(app)
    response = client.get("/api/detail_titles?dataset=0&view=1")
    assert response.status_code == 200
    result = response.json()
    assert "3" in result
    assert result["3"]["title"] == "Close-up (3 frames)"
    assert result["3"]["type"] == -1


def test_detail_titles_empty_view(tmp_path, reset_app_state):
    """Requesting a view that has no detail icons returns an empty dict."""
    gallery_file, ds = _make_synthetic_walk_dataset(tmp_path, [5])
    _app_state["gallery"] = ds
    _app_state["gallery_path"] = gallery_file

    client = TestClient(app)
    response = client.get("/api/detail_titles?dataset=0&view=999")
    assert response.status_code == 200
    assert response.json() == {}


def test_detail_titles_no_gallery(reset_app_state):
    """Requesting detail_titles when the gallery is not loaded returns 503."""
    _app_state["gallery"] = None

    client = TestClient(app)
    response = client.get("/api/detail_titles?dataset=0&view=1")
    assert response.status_code == 503


@require_gallery
@require_names
def test_detail_titles_gallery_mode_real(reset_app_state):
    """Gallery-mode detail_titles returns non-empty titles from the real NAMES file."""
    from domesday.parser import load_gallery

    ds = load_gallery(GALLERY_PATH)
    _app_state["gallery"] = ds
    _app_state["gallery_path"] = GALLERY_PATH
    _app_state["names_path"] = NAMES_PATH

    # Find the first view that has detail icons
    view_with_details = None
    for v, node in ds.nodes.items():
        if node.details:
            view_with_details = v
            break
    if view_with_details is None:
        pytest.skip("No gallery nodes with detail icons found")

    client = TestClient(app)
    response = client.get(f"/api/detail_titles?dataset=0&view={view_with_details}")
    assert response.status_code == 200
    result = response.json()
    assert len(result) > 0, "Expected at least one detail title"
    for key, val in result.items():
        assert isinstance(val.get("title"), str), f"title should be str for key {key}"
        assert val["title"], f"title should be non-empty for key {key}"
        assert "type" in val

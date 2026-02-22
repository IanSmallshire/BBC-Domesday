"""Data models for BBC Domesday Walk/Gallery datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DetailIcon:
    x: int  # screen X (BBC graphics units 0-1280)
    y: int  # screen Y
    item_offset: int  # word offset into NAMES file (gallery items)


@dataclass
class PlanPosition:
    x: int  # 0-4095 (from plan x_word bits 0-11)
    y: int  # 0-4095 (from plan y_word bits 0-11)
    plan_number: int  # 0-15 (from y_word bits 12-15)
    base_direction: int  # 0-7 (raw x_word bits 12-15); used with view to compute compass bearing


@dataclass
class LinkTarget:
    """Points to a sub-dataset reachable by following a cross-dataset link."""

    byte_offset: int  # byte offset into the source file (GALLERY, DATA1, or DATA2)
    use_data2: bool  # True → DATA2, False → same file as current dataset (gallery) or DATA1


@dataclass
class Node:
    view: int  # 1-based view index within its dataset
    frame: int  # absolute LaserDisc frame number
    forward: int | None  # view number to move to when going forward (None = dead-end)
    linked_dataset: bool  # True if forward leads into a different sub-dataset
    link_target: LinkTarget | None  # populated when linked_dataset is True
    details: list[DetailIcon] = field(default_factory=list)
    plan: PlanPosition | None = None


@dataclass
class WalkDataset:
    source_file: Path
    syslev: int  # 1 = gallery, else walk
    base_view: int  # LaserDisc frame offset for view frames
    base_plan: int  # LaserDisc frame offset for plan frames
    initial_view: int  # first view to show (from ctable entry 0)
    nodes: dict[int, Node]  # view → Node
    dtable_byte: int = 0  # byte offset of dtable within dataset (from header r(20))

    def get_frame_path(self, frame: int, jpgimg_root: Path) -> Path:
        folder = f"{frame // 1000:02d}"
        return jpgimg_root / folder / f"{frame}.jpg"

    def get_plan_frame(self, plan_number: int) -> int:
        """Return absolute LaserDisc frame number for the given plan image."""
        return self.base_plan + self.base_view + plan_number

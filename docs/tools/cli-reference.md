# CLI Tool Reference

Both tools auto-detect the disc type from the data directory contents:
- **National disc** (`data/NationalA/`) — has `VFS/GALLERY` and `VFS/HIERARCHY`
- **Community disc** (`data/CommN/` or `data/CommS/`) — has `VFS/MAPDATA1` and `VFS/INDEX`

---

## `domesday.catalogue` — Disc Catalogue

Lists every content item on the disc with its type and disc address.

### National disc

**Reads**: `VFS/HIERARCHY` (thesaurus tree) + `VFS/NAMES`

```bash
# Human-readable text → stdout
python -m domesday.catalogue

# Save as plain text
python -m domesday.catalogue --format text > catalogue.txt

# Save as CSV
python -m domesday.catalogue --format csv > catalogue.csv

# Save as JSON
python -m domesday.catalogue --format json > catalogue.json

# Filter by type name (substring match)
python -m domesday.catalogue --type Essay
python -m domesday.catalogue --type Photo
python -m domesday.catalogue --type "Grid map"

# Non-default data directory
python -m domesday.catalogue --data /path/to/NationalA
```

**Text output** (grouped by thesaurus path):
```
People > Households > Composition
  [Photo           ] Family Groups
  [Essay           ] The British Family

── Summary ──────────────────────────────────────────────────────
Total datasets: 12161
  Data/Chart             1802
  Essay                  1953
  Photo                   557
  ...
```

**CSV columns**: `path`, `name`, `type`, `address_hex`, `record_no`

### Community disc

**Reads**: `VFS/NAMES` (no thesaurus — items are flat, not hierarchical)

```bash
# Human-readable text
python -m domesday.catalogue --data data/CommN

# Save as CSV
python -m domesday.catalogue --data data/CommN --format csv > comm_catalogue.csv

# Save as JSON
python -m domesday.catalogue --data data/CommN --format json > comm_catalogue.json

# Filter by type
python -m domesday.catalogue --data data/CommN --type "Community Text"
python -m domesday.catalogue --data data/CommN --type "Community Photo"

# South disc
python -m domesday.catalogue --data data/CommS
```

**Text output** (alphabetical, grouped by type):
```
Community Texts
  [Frame   3357] "Gypsies" in block 04480453
  [Frame  15399] "COUNTRY VENTURE" TEBAY
  ...

Community Photos
  [Frame  34330] "BOTHY NIGHT",LADY, SANDAY.
  ...

── Summary ──────────────────────────────────────────────────────
Total items: 38768
  Community Text        31899
  Community Photo        6869
```

**CSV columns**: `name`, `type`, `frame`, `page_or_pic`, `record_no`

### Pre-generated output files

The following files are committed at the repository root (National disc only):

| File | Format | Command |
|---|---|---|
| `catalogue.txt` | Text | `python -m domesday.catalogue --format text > catalogue.txt` |
| `catalogue.csv` | CSV | `python -m domesday.catalogue --format csv > catalogue.csv` |
| `catalogue.json` | JSON | `python -m domesday.catalogue --format json > catalogue.json` |

---

## `domesday.frame_index` — Disc Frame Index

Looks up all known metadata for any LaserDisc frame number, aggregating all
frame sources for the disc type.

### National disc record types

| `--type` value | Source | Key fields |
|---|---|---|
| `gallery_view` | GALLERY offset 0 | `walk_name`, `view_number` |
| `walk_view` | GALLERY embedded sub-datasets | `walk_name`, `view_number`, `path` |
| `plan` | Plan tables in GALLERY datasets | `walk_name`, `plan_number` |
| `photo` | Catalogue type-8 → DATA1/DATA2 | `short_caption`, `long_caption`, `photo_index`, `photo_count` |
| `essay_figure` | Catalogue type-6/7 → DATA1/DATA2 | `essay_title`, `figure_page`, `figure_index` |

### Community disc record types

| `--type` value | Source | Key fields |
|---|---|---|
| `map_image` | MAPDATA1 `mapno` fields | `map_level`, `map_easting`, `map_northing` |
| `data_bundle` | MAPDATA1 `ptaddress` / submap ptaddress fields | `map_level`, `map_easting`, `map_northing` |
| `community_photo` | Photo frames extracted from data bundle photo sections | `short_caption`, `map_level`, `map_easting`, `map_northing` |

### Commands

```bash
# Single frame lookup
python -m domesday.frame_index --frame 803
python -m domesday.frame_index --data data/CommN --frame 18791

# Filter by record type
python -m domesday.frame_index --type gallery_view
python -m domesday.frame_index --data data/CommN --type map_image
python -m domesday.frame_index --data data/CommN --type community_photo

# Dump entire index as JSON
python -m domesday.frame_index --format json --output frame_index.json
python -m domesday.frame_index --data data/CommN --format json --output comm_frame_index.json

# Dump as CSV
python -m domesday.frame_index --format csv --output frame_index.csv
python -m domesday.frame_index --data data/CommN --format csv --output comm_frame_index.csv

# South disc
python -m domesday.frame_index --data data/CommS --frame 18791
```

### Single-frame text output examples

National disc:
```
Frame 42138
  [photo] British Films of the 80s
  Path:   CULTURE > ARTS & ENTERTAINMENT > CINEMA > FILM INDUSTRY
  Photo:  3 of 7
  Short:  "Film crew on location..."
  Long:   "The British film industry in the 1980s..."
```

Community disc:
```
Frame 18791
  [map_image] L0 map
  Easting:  0 km
  Northing: 0 km

Frame 2488
  [data_bundle] L2
  Easting:  3600 km
  Northing: 11100 km
```

### Pre-generated output files

| File | Format | Content |
|---|---|---|
| `frame_index_full.txt` | Text, one entry per frame 1–54000 | National disc only — see generation script below |

The `frame_index_full.txt` file was generated with:

```python
from pathlib import Path
from domesday.frame_index import build_frame_index, _format_frame_text

records = build_frame_index(Path("data/NationalA"))
by_frame = {}
for r in records:
    by_frame.setdefault(r.frame, []).append(r)

lines = []
for frame in range(1, 54001):
    lines.append(_format_frame_text(by_frame.get(frame, [])))

Path("frame_index_full.txt").write_text("\n\n".join(lines), encoding="utf-8")
```

---

## Data bundle addressing (Community disc internals)

Community disc data bundles are stored in DATA1/DATA2 as sequential 6144-byte
frames. The base frame address is discovered at runtime from the minimum
`ptaddress` value found across all MAPDATA1 records:

```
d1_base   = min(all ptaddress values in MAPDATA1)
d1_frames = len(DATA1) / 6144
d2_base   = d1_base + d1_frames

DATA1 offset for ptaddr  =  (ptaddr - d1_base) * 6144
DATA2 offset for ptaddr  =  (ptaddr - d2_base) * 6144
```

`ptaddress` values outside both ranges refer to frames on the **other disc side**
(North ↔ South) and are silently skipped by the tool.

For CommN: `d1_base = 2488`, `d1_frames = 17080`, `d2_base = 19568`.

# Community Disc File Formats

Sources: `build/src/H/cmhd.h`, `cphd.h`, `cfhd.h`, `cm2hd.h`, `cm3hd.h`;
`build/src/CM/cm0.b`, `map1.b`, `map2.b`, `compho1.b`, `compho2.b`;
`build/src/CF/find3.b`, `find7.b`, `find8.b`

---

## File Inventory

Community disc data (`data/CommN/`, `data/CommS/`) differs substantially from the National disc:

| File | National (`NationalA/VFS/`) | Community (`CommN/` or `CommS/`) |
|---|---|---|
| `NAMES` | Yes — 36 bytes/rec, uint32 address | Yes — 36 bytes/rec, **different encoding** |
| `HIERARCHY` | Yes — thesaurus tree | **No** |
| `GALLERY` | Yes — walk sub-datasets | **No** |
| `DATA1` / `DATA2` | Yes — essays and photo sets | Yes — data bundles |
| `MAPDATA1` | **No** | Yes — map records |
| `INDEX` | **No** | Yes — full-text inverted index |
| `GAZETTEER` | **No** | Yes — place name search |

---

## Key Convention: Frame Numbers as Addresses

**Critical difference from the National disc.**

- **National**: bytes 32–35 of a NAMES record are a **32-bit byte offset** into DATA1 or DATA2.
- **Community**: bytes 32–33 of a NAMES record are a **16-bit LaserDisc frame number**.

Community disc data bundles (photos and text) live as individual video frames on the LaserDisc, not as byte ranges in a flat file. The frame number is the seek address sent to the disc player.

---

## Community NAMES Record (36 bytes)

Same total size as the National NAMES record, but byte 31 and bytes 32–35 encode differently.

| Byte range | Size | Field | Notes |
|---|---|---|---|
| 0–30 | 31 | `title` | Latin-1 string, space/null padded |
| 31 | 1 | `type_byte` | **Bit 7** = 0 → text item; bit 7 = 1 → photo item. Bits 0–6 = page or picture number. |
| 32–33 | 2 | `frame` | uint16 LE — LaserDisc frame number of the data bundle |
| 34–35 | 2 | (padding) | Unused |

There are **no numeric item type codes** (0–10) as on the National disc. The only discrimination is bit 7 of byte 31:

```
byte31 & 0x80 == 0  →  text item   (bits 0–6 = page number)
byte31 & 0x80 != 0  →  photo item  (bits 0–6 = picture number)
```

Source: `find8.b` — `g.context!m.itemaddress := g.ut.unpack16(q, 32)` where `q` is the 36-byte record.

---

## MAPDATA1 — Map Record File

A flat file of **816-byte fixed-size slots**, one per map.

### Slot addressing

```
slot_offset  =  (mapno − map.L0) × 816      where map.L0 = 18791
blen         =  uint16 LE at slot_offset       (actual byte length of the record)
record_data  =  bytes [slot_offset+2 … slot_offset+2+blen−1]
```

Constants (`cmhd.h`):
- `m.cm.maprec.size = 816` bytes per slot
- `map.L0 = 18791` — frame number of the first L0 map; doubles as the mapno-to-slot base
- `map.L1 = map.L0 + 6 = 18797`

Source: `map1.b` — `readtospace()`.

### Map Record Binary Layout

All fields are little-endian. The record begins with a fixed 18-byte head followed by a variable-length tail.

| Offset | Size | Field | Type | Notes |
|---|---|---|---|---|
| 0 | 2 | `length` | int16 | Actual byte length of this record |
| 2 | 2 | `mapno` | uint16 | Map number (= LaserDisc frame for this map image) |
| 4 | 2 | `easting` | uint16 | Grid easting (km) |
| 6 | 2 | `northing` | uint16 | Grid northing (km) |
| 8 | 2 | `parent` | uint16 | `mapno` of the parent in the hierarchy |
| 10 | 1 | `level` | uint8 | Map level 0–5 (see hierarchy in `community-modules.md`) |
| 11 | 1 | `flags` | uint8 | Bit 0 = has texts; bit 1 = has photos; bit 2 = has type-b icon vec |
| 12 | 2 | `ptaddress` | uint16 | Frame number of this map's data bundle (0 = none) |
| 14 | 1 | `M` | uint8 | Submap matrix easting dimension |
| 15 | 1 | `N` | uint8 | Submap matrix northing dimension |
| 16 | 2 | `base_mapno` | uint16 | Base frame for computing submap frame numbers |

Variable-length tail (immediately following the fixed head, in order):

| Section | Size | Notes |
|---|---|---|
| `submap_idx[M×N]` | M×N bytes | Relative submap indices; value ≥ 254 = absent or on the other disc side |
| `ptaddresses[M×N]` | M×N × 2 bytes | uint16 data bundle frame number per submap cell |
| `text_bitmap` | 1 + ceil(MN/8) bytes | 1-byte total-length prefix + bitmap (1 bit per submap: 1 = has text) |
| `photo_bitmap` | 1 + ceil(MN/8) bytes | Same structure as text bitmap |
| `icon_vec` | 1 + n bytes | 1-byte total-length prefix + main icon vector data |
| `L_icon_list` | 2 + m bytes | uint16 total-length prefix + per-submap entries: 1-byte submap index + 1-byte length + data |

Source: `map2.b` — `getmapinfo()`, `readsubmap()`, `readbitmaps()`.

---

## Data Bundle — Photo/Text Frame Format

A "data bundle" is a single LaserDisc frame accessed by frame number. Each bundle is up to **6,144 bytes** (`m.cp.framesize = 6×1024`). It contains interleaved photo and text data for one map location.

### 14-byte Header

| Offset | Size | Field | Type | Notes |
|---|---|---|---|---|
| 0 | 1 | `level` | uint8 | Map level this data belongs to |
| 1 | 1 | `type` | uint8 | Data type code |
| 2 | 2 | `picoff` | uint16 | Byte offset within the bundle to the photo section |
| 4 | 2 | `textoff` | uint16 | Byte offset within the bundle to the text section |
| 6 | 2 | `map_no` | uint16 | Map frame number this data belongs to |
| 8 | 2 | `maprec_no` | uint16 | Map record number |
| 10 | 2 | `easting` | uint16 | Grid easting (km) |
| 12 | 2 | `northing` | uint16 | Grid northing (km) |

### Photo Section (at byte `picoff`)

| Offset (relative to `picoff`) | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | `npics_raw` | uint16; bit 7 of high byte set → 8-line long captions |
| 2 | npics × 2 | `frames[]` | uint16 LaserDisc frame per photo (1-indexed in BCPL) |
| 2 + npics×2 | npics × 30 | `short_captions[]` | 30 bytes each (`m.cp.sclength = 30`) |
| above + npics×30 | npics × lcs | `long_captions[]` | lcs = 8×39 if flag set, else 4×39 bytes each |

- `npics = npics_raw & 0x7F`
- Photo frame n is at `picoff + 2 + n×2` (n is 0-based here; BCPL source uses 1-based indexing)

Source: `compho1.b` — `init.data.buffer()`, `display.picture()`.

---

## INDEX — Full-Text Inverted Index

Opened by `g.cf.dy.init()` in `find7.b`. Covers all text and photo item titles on the community disc.

- 4 levels (`m.cf.indexlevels = 4`)
- Query terms are Porter-stemmed before lookup (`find5.b` contains the stemmer)

### D-value Format (8 bytes each, `m.biisize = 8`)

Each index posting is an 8-byte "D-value":

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0–2 | 3 | `names_recno` | NAMES record number; multiply by 36 to get byte offset in NAMES file |
| 3 | 1 | `level` | Map level (0–5); zeroed out before multiplying to isolate `names_recno` |
| 4–5 | 2 | `easting` | uint16 easting (km) |
| 6–7 | 2 | `northing` | uint16 northing (km) |

Source: `find3.b` — `g.cf.extracttitles()`:
```bcpl
// zero the level byte, then names_recno × 36 = byte offset into NAMES
```

Maximum results: 101 best matches (`m.cf.maxmatches`); 21 titles per display page (`m.cf.titlesperpage`).

---

## GAZETTEER — Place Name Index

Opened by `g.cf.dy.init()` alongside INDEX and NAMES. Used to resolve a typed place name to a grid reference, which is then used to geographically filter INDEX results.

- File size is stored as a 2-word (32-bit) value at open time
- Queried separately from the full-text INDEX

Binary layout not determined from available source analysis.

---

## Grid Reference System

| Bit of easting/northing | Meaning |
|---|---|
| Bit 15 set | Northern Ireland grid (NI) |
| Bit 14 set | Channel Islands |
| Neither set | Great Britain national grid |

Easting and northing values are in kilometres within the chosen grid system.

Source: `cm3hd.h`, `cm0.b`.

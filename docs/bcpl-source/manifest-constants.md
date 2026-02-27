# BCPL Manifest Constants Reference

All manifest constants are defined in header files under `build/src/H/`. BCPL manifests are compile-time integer constants (like C `#define`).

---

## `nwhd.h` — National Walk Header

Used throughout `walk1.b` and `walk2.b`.

### Screen Geometry

| Constant | Value | Meaning |
|----------|-------|---------|
| `film.start` | 705 | LaserDisc frame number where the gallery intro film begins |
| `thirdwidth` | `m.sd.disw/3` = 426 | One third of the BBC Micro screen width (graphics units) |
| `lmarg` | 372 | X coordinate marking end of left third of TV screen |
| `rmarg` | `m.sd.disW − lmarg` = 908 | X coordinate marking start of right third |
| `m.titlesize` | 36 | Byte size of one NAMES file entry |
| `m.lens.size` | 45 | Radius of magnifying glass hit area (graphics units) |
| `m.picwidth` | 64 | Average gallery picture width (graphics units) |
| `m.picheight` | 64 | Average gallery picture height (graphics units) |
| `m.datasize` | `13000/BYTESPERWORD` | Size of `g.nw` data body (words) |

### `g.nw` Vector Slot Offsets (negative indices)

| Constant | Index | Meaning |
|----------|-------|---------|
| `view` | −1 | Current view number (1-based) |
| `cubase` | −2 | Base of close-up chain in dtable |
| `cu` | −3 | Close-up chain pointer (0 = not in close-up) |
| `fiddlemenu` | −4 | True when menu bar needs redrawing |
| `wmess` | −5 | True when message area has content |
| `wdisp` | −6 | True when display area has content |
| `vrestore` | −7 | True when video needs unmuting |
| `ltable` | −8 | Link table word offset from `g.nw` |
| `ctable` | −9 | Control table word offset from `g.nw` |
| `ptable` | −10 | Plan table word offset from `g.nw` |
| `dtable` | −11 | Detail table word offset from `g.nw` |
| `m.baseview` | −12 | First LaserDisc frame of view images |
| `m.baseplan` | −13 | First LaserDisc frame of plan images |
| `m.syslev` | −14 | 1 = gallery, else walk |
| `addr1` | −15 | High 16-bit word of current dataset address |
| `addr0` | −16 | Low 16-bit word of current dataset address |
| `gallerydetail` | −17 | True if detail entered from gallery context |
| `base.pos` | −18 | Plan table position on entry to current walk |
| `m.h` | 18 | Number of header slots (size of negative-index region) |

---

## `nehd.h` — National Essay Header

Used by the National Essay (NE) module.

### Essay Content Types

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.ne.nessay` | 6 | Text-only national essay (NAMES type code) |
| `m.ne.picessay` | 7 | Picture national essay (NAMES type code) |
| `m.ne.text` | 1 | Page type: text |
| `m.ne.picture` | 2 | Page type: picture |
| `m.ne.for` | 1 | Paging direction: forward |
| `m.ne.back` | 2 | Paging direction: backward |
| `m.ne.firstpage` | 1 | `at.end` flag value: on first page |
| `m.ne.lastpage` | 2 | `at.end` flag value: on last page |
| `m.ne.invalid` | −1 | Invalid photo pointer sentinel |

### Essay Data Structure Offsets

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.ne.dataset.header.size` | 28 | Bytes before figure records begin |
| `m.ne.photo.data.size` | 200 | Total bytes in figure record block (25 × 8) |
| `m.ne.page.no.offset` | 228 | Byte offset to `num_pages` field |
| `m.ne.article.title.offset` | 230 | Byte offset to first title entry |
| `m.ne.title.size` | 30 | Bytes per title entry |
| `m.ne.phosize` | 25 | Maximum number of figure records |
| `m.ne.rsize` | 8 | Bytes per figure record |

### Text Geometry

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.ne.nolines` | 22 | Text lines per page |
| `m.ne.lcaplen` | 39 | Characters per line in long captions |
| `m.ne.scaplen` | 30 | Characters in a short caption |
| `m.ne.capsize1` | 8 | Lines in a long long-caption |
| `m.ne.capsize2` | 4 | Lines in a shorter long-caption |
| `m.ne.maxtitles` | 20 | Max titles displayed on contents page |
| `m.ne.maxpage` | 99 | Max page number on contents page |

### Output Modes

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.ne.none` | 0 | No output |
| `m.ne.print` | 1 | Print to printer |
| `m.ne.write` | 2 | Write to floppy disc |
| `m.ne.screen` | 3 | Display on screen |

### Context Area (`G.ne.s`) Slot Offsets

| Constant | Index | Meaning |
|----------|-------|---------|
| `m.ne.type` | 0 | Essay type (6 or 7) |
| `m.ne.write.pending` | 1 | Write-to-disc pending flag |
| `m.ne.gone.to.photo` | 2 | Currently viewing embedded photo |
| `m.ne.gone.to.help` | 3 | Currently in help |
| `m.ne.nopages` | 4 | Number of pages in article |
| `m.ne.max.pages` | 5 | Max text pages in buffer |
| `m.ne.firstinbuff` | 6 | First page number in buffer |
| `m.ne.notitles` | 7 | Count of non-null titles |
| `m.ne.photoptr` | 8 | Pointer into figure data |
| `m.ne.pagetype` | 9 | Current page type |
| `m.ne.fullset` | 10 | Flag: full photo set to insert |
| `m.ne.pictno` | 11 | Current picture number in set |
| `m.ne.nopics` | 12 | Number of pictures in dataset |
| `m.ne.D1.handle` | 13 | File handle for DATA1 |
| `m.ne.D2.handle` | 14 | File handle for DATA2 |
| `m.ne.desc.size` | 15 | Lines in current long caption |
| `m.ne.at.end` | 16 | First/last page flag |
| `m.ne.essay.no` | 17 | Current essay number (1, 2, or 3) |
| `m.ne.pagebuff` | 18 | Pointer to page buffer |
| `m.ne.text.is.data2` | 19 | True if text data is in DATA2 |
| `m.ne.photo.is.data2` | 20 | True if photo data is in DATA2 |
| `m.ne.itemaddress` | 30 | 32-bit address of text dataset |
| `m.ne.firstaddr` | 32 | Cached original item address |
| `m.ne.photoaddr` | 34 | 32-bit address of photo dataset |

---

## `nphd.h` — National Photo Header

Used by the National Photo (NP) module.

### Photo Data Structure Offsets

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.np.num.pics.off` | 28 | Byte offset to `num_pics_raw` field |
| `m.np.sclength` | 30 | Characters per short caption |
| `m.np.lclength` | 39 | Characters per long caption line |
| `m.np.small.lc` | 4 | Lines per long caption (small variant) |
| `m.np.large.lc` | 8 | Lines per long caption (large variant) |
| `m.np.max.shorts` | 100 | Maximum short captions buffered |
| `m.np.frame.size` | 2 | Bytes per frame number entry |

### Buffer Sizes

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.np.short.buff.size` | `100 × 30 = 3000` | Short caption buffer bytes |
| `m.np.rbuff.size` | `8 × 39 = 312` | Long caption buffer bytes (one caption) |
| `m.np.tbuff.size` | `40` | Output line buffer bytes |

### Screen Position Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.np.LHS` | `m.sd.disw/3` = 426 | Left third of display |
| `m.np.RHS` | `(m.sd.disw×2)/3` = 853 | Right third of display |
| `m.np.charwidth` | 32 | Graphics units per character |
| `m.np.scYpos` | `m.sd.linW` = 40 | Y position for short caption |
| `m.np.lcYpos` | 0 | Y position for long caption |

---

## `sdhd.h` — Screen Definitions

Defines the BBC Micro graphics coordinate system used throughout the Domesday system.

### Screen Area Positions

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.sd.menu` | 1 | Menu bar area identifier |
| `m.sd.display` | 2 | Display area identifier |
| `m.sd.message` | 3 | Message area identifier |
| `m.sd.menX0` | 0 | Menu bar X origin |
| `m.sd.menY0` | 0 | Menu bar Y origin |
| `m.sd.disX0` | 0 | Display area X origin |
| `m.sd.disY0` | 76 | Display area Y origin (above menu bar) |
| `m.sd.mesX0` | 0 | Message area X origin |
| `m.sd.mesY0` | 976 | Message area Y origin (top of screen) |

### Screen Area Sizes

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.sd.menw` | 1280 | Menu bar width |
| `m.sd.menh` | 76 | Menu bar height |
| `m.sd.disw` | 1280 | Display area width |
| `m.sd.dish` | 888 | Display area height |
| `m.sd.mesw` | 1280 | Message area width |
| `m.sd.mesh` | 48 | Message area height |

### Text Layout

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.sd.charwidth` | 32 | Character width in graphics units |
| `m.sd.charheight` | 32 | Character height in graphics units |
| `m.sd.linw` | 40 | Y spacing between text lines |
| `m.sd.charsperline` | 40 | Characters per screen line |
| `m.sd.displines` | 22 | Text lines in display area |
| `m.sd.linelength` | 39 | Characters per text line in data files |
| `m.sd.pagelength` | 858 | Total chars per page (22 × 39) |

### Plot and Colour Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.sd.plot` | 1 | Draw in foreground colour |
| `m.sd.clear` | 3 | Draw in background colour (erase) |
| `m.sd.invert` | 2 | Draw in inverse colour |
| `m.sd.on` | true | Mouse pointer on |
| `m.sd.off` | false | Mouse pointer off |
| `m.sd.mag.glass` | 3 | Magnifying glass icon ID |
| `m.sd.cross1` | 1 | Blue cross icon ID |
| `m.sd.cross2` | 2 | Yellow cross icon ID |
| `m.sd.act` | 1 | Menu box active |
| `m.sd.wBlank` | `#x1FF` | Menu box blank |
| `m.sd.wClear` | 25 | Menu box shows "Clear" |
| `m.sd.black` | 0 | Palette colour: black |
| `m.sd.yellow` | 1 | Palette colour: yellow |
| `m.sd.blue` | 2 | Palette colour: blue |
| `m.sd.cyan` | 3 | Palette colour: cyan |

---

## `sthd.h` — State Table

Defines all possible system states. The Walk/Gallery module uses states 33–41.

### Walk/Gallery States

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.st.gallery` | 33 | Gallery view/move entry point |
| `m.st.galmove` | 34 | Gallery moving (navigation active) |
| `m.st.gplan1` | 35 | Gallery plan view (phase 1) |
| `m.st.gplan2` | 36 | Gallery plan view (phase 2) |
| `m.st.walk` | 37 | Walk view/move entry point |
| `m.st.walmove` | 38 | Walk moving (navigation active) |
| `m.st.wplan1` | 39 | Walk plan view (phase 1) |
| `m.st.wplan2` | 40 | Walk plan view (phase 2) |
| `m.st.detail` | 41 | Detail/close-up view |

### Item Type Destination States

| Constant | Value | Target item type |
|----------|-------|-----------------|
| `m.st.datmap` | 15 | Mappable data (types 1–3) |
| `m.st.chart` | 28 | National chart (type 4) |
| `m.st.ntext` | 43 | National essay (types 6, 7) |
| `m.st.nphoto` | 44 | National photo set (type 8) |
| `m.st.walk` | 37 | Surrogate walk (type 9) |
| `m.st.film` | 42 | Film sequence (type 10) |

### Other Referenced States

| Constant | Value | Meaning |
|----------|-------|---------|
| `m.st.startstop` | 0 | Start/Stop screen (special: no intro film if last state) |
| `m.st.conten` | 12 | Contents |
| `m.st.uarea` | 13 | User area |
| `m.st.area` | 14 | Area selection |
| `m.st.nfindm` | 31 | National find (menu) |
| `m.st.nostates` | 53 | Maximum state value |

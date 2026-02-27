# Module Reference

This section documents all BCPL modules in the Domesday system beyond the National Walk (NW) module already covered in the main documentation. Each subsection covers one directory of source under `build/src/`.

## Module Inventory

```mermaid
graph TD
    subgraph "System Layer"
        KE["KE — Kernel\nState machine dispatcher"]
        DH["DH — Data Handler\nVFS/ADFS file I/O"]
        VH["VH — Video Handler\nLaserDisc player"]
        SC["SC — Screen\nGraphics primitives"]
        UT["UT — Utilities\n32-bit math, grid refs"]
        HE["HE — Help\nHelp overlay"]
        SI["SI — State Init\nBuild-time table generator"]
    end

    subgraph "Community Disc"
        CM["CM — Community Map\nMulti-level zoomable maps"]
        CO["CO — Map Options\nScale / Distance / Area"]
        CF["CF — Community Find\nFull-text keyword search"]
        CP["CP — Community Photo\nPhoto + text captions"]
        CT["CT — Community Text\nSchools and AA text"]
    end

    subgraph "National Disc"
        NA["NA — National Area\nGeographic area selection"]
        NC["NC — National Chart\nStatistical charts"]
        NE["NE — National Essay\nLong-form text articles"]
        NF["NF — National Find\nKeyword search"]
        NM["NM — National Mappable\nStatistical map overlay"]
        NN["NN — National Analysis\nCorrelation / ranking"]
        NP["NP — National Photo\nPhoto set viewer"]
        NT["NT — National Contents\nThesaurus/hierarchy"]
        NV["NV — National Video\nFilm viewer (CLV)"]
        NW["NW — National Walk\nSurrogate walks ✓"]
    end

    KE --> DH
    KE --> VH
    KE --> SC
    KE --> UT
```

## Quick Reference: Source Directories

| Dir | Module | BCPL Files | Header(s) | Description |
|-----|--------|-----------|-----------|-------------|
| `KE/` | Kernel | init, root, general, sram, kernel1, kernel2 | `glhd.h` | Core state machine |
| `DH/` | Data Handler | dh1, dh2, seldisc, userdata | `dhhd.h`, `dhphd.h` | File I/O |
| `VH/` | Video Handler | vh1, vh2, vh3 | `vhhd.h` | LaserDisc control |
| `SC/` | Screen | graph1-2, text1-3, input, menu, mouse, icon, etc. | `sdhd.h` | Graphics |
| `UT/` | Utilities | utils1-4, calc32b, grid1-2, print, write, bookmark | `uthd.h`, `grhd.h` | Utilities |
| `HE/` | Help | help0-1, helpA-D, helpinit, htext1-7 | `hehd.h` | Help overlay |
| `SI/` | State Init | stinit, r*/s* pairs | `stphd.h` | Build tool |
| `CM/` | Community Map | map0-6, cm0-4 | `cmhd.h`, `cm2hd.h`, `cm3hd.h` | Community maps |
| `CO/` | Map Options | mapopt1-8 | `cm3hd.h` | Distance/area |
| `CF/` | Community Find | find0-8 | `cfhd.h` | Full-text search |
| `CP/` | Community Photo | cominit, compho1-2 | `cphd.h` | Photo+text |
| `CT/` | Community Text | ctext1-4, aatext1-4, gentext1-2 | `cphd.h` | Text content |
| `NA/` | National Area | area0-4, area | `nahd.h` | Area selection |
| `NC/` | National Chart | chart0-8 | `nchd.h` | Charts |
| `NE/` | National Essay | natinit, ntext1-6 | `nehd.h` | Essays |
| `NF/` | National Find | find0-7, find9 | `nfhd.h` | National search |
| `NM/` | Nat. Mappable | 18 files | `nmhd.h` + privates | Stat. maps |
| `NN/` | Nat. Analysis | 30 files | `nmhd.h` | Analysis |
| `NP/` | National Photo | natinit, natpho1-3 | `nphd.h` | Photo sets |
| `NT/` | Nat. Contents | nt0-3 | `nthd.h` | Thesaurus |
| `NV/` | National Video | nv0-2 | `nvhd.h` | Film playback |
| `NW/` | National Walk | walk1-2 | `nwhd.h` | Walks ✓ |

See per-module documents in this folder for details.

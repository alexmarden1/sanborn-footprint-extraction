# Sanborn footprints

Turn georeferenced **Sanborn fire insurance map** sheets into a GIS layer of
building footprints, each one labelled with the construction material the map
says it was built from.

Sanborn surveyors colour-coded every building on the sheet: pink for brick,
yellow for wood frame, grey-brown for adobe, blue for iron or fireproof
construction, green for "special" construction. Those colours are still sitting
in the scans. This pipeline reads them, traces each building, squares up the
outline, removes the duplicate copies created by overlapping sheets, and writes
GeoJSON + shapefile you can open in QGIS or ArcGIS.

Built for the **1908 Sanborn atlas of El Paso, Texas** (43 georeferenced sheets
from the University of Texas Libraries), which yields **7,538 building
footprints, 55.5 hectares of built area**, in about ten minutes on a laptop:

| material | buildings | share |
| --- | --- | --- |
| frame (wood) | 4,596 | 61.0% |
| brick | 2,224 | 29.5% |
| adobe | 572 | 7.6% |
| iron / fireproof | 82 | 1.1% |
| special construction | 64 | 0.8% |

Median footprint 24.9 m². 84% of outlines came out fully orthogonal; 3,263
porches and wings are linked to the building they hang off.

It is not El Paso-specific. Any set of georeferenced sheets that uses the
standard Sanborn colour key should run through it, though you will probably want
to tune the colour thresholds for your own scans (see
[Tuning](#tuning-for-your-own-sheets)).

---

## Quickstart

```bash
git clone https://github.com/<your-username>/sanborn-footprint-extraction.git
cd sanborn-footprint-extraction

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put your georeferenced sheets (one GeoTIFF per sheet, EPSG:4326) in
`data/sheets/`, then:

```bash
python 01_extract_sheets.py        # trace each sheet   -> work/sheets/*.json
python 02_build_footprints.py      # assemble the layer -> output/*.geojson, *.shp
```

That's the whole pipeline. Open `output/sanborn_footprints.geojson` in QGIS,
style it by the `material` field, and drag a sheet raster underneath to check the
alignment.

**Try one sheet first.** A full run at full resolution takes a few minutes and
about 2 GB of RAM:

```bash
python 01_extract_sheets.py --limit 1 --res 0.25
python 02_build_footprints.py --name trial
```

If your sheet folder holds other rasters too (a mosaic, an index plate), narrow
the match: `--pattern 'utlmaps_*.tif'`. Anything listed in `SKIP_SHEETS` in
`config.py` is ignored as well.

### Where the paths live

Defaults are `data/sheets/`, `work/sheets/`, `output/`, all set in `config.py`.
Override any of them without editing the file:

```bash
python 01_extract_sheets.py --sheet-dir /mnt/maps/el_paso_1908 --work-dir /tmp/work
SANBORN_SHEET_DIR=/mnt/maps/el_paso_1908 python 01_extract_sheets.py
```

Step 1 writes one JSON per sheet and **skips sheets it has already traced**, so
an interrupted run resumes where it stopped, and you can split the work across
several terminals with `--start`/`--end`. Add `--overwrite` to force a re-trace.

Step 2 reads only those JSON files, never the rasters. It runs in seconds, which
makes it cheap to re-run while you tune the assembly thresholds.

---

## How it works

### 1. Colour to material (`classify.py`)

Hand-tuned HSV rules, not a trained model: the palette is a published standard,
the rules are auditable, and nobody has to label training data. Black line work
and text are excluded by value, and blank paper is found relative to *local*
paper brightness (the 80th percentile of value in the tile), so a tea-stained
sheet does not read as one enormous adobe building.

To see what the rules are doing on your scans:

```bash
python classify.py data/sheets/my_sheet.tif preview.png 8   # 8 = downsample factor
```

### 2. Tracing, one sheet at a time (`01_extract_sheets.py`)

Each sheet is read whole at 0.10 m/px, so no building is ever cut in half by a
processing tile. Two details matter more than they look:

- **Objects come from the union of all material classes, then get partitioned by
  material.** Tracing each colour independently instead leaves a ragged fringe
  wherever two colours meet. This way a frame porch cleanly separates from its
  brick host while the building's outer wall stays smooth.
- **Ink lines and anti-aliased edges inside a building inherit the nearest
  material.** Left unfilled they would punch fake courtyards through footprints.

### 3. Deduplicating and regularizing (`02_build_footprints.py`, `regularize.py`)

Sanborn sheets overlap — in the El Paso set, 61% of the mapped bounding-box area
falls on more than one sheet — so the same building gets traced repeatedly, and
the copies near a sheet margin are clipped by it. The fix is geometric: sort
every candidate by how far its centroid sits from its own sheet's edge, then
greedily accept the most interior copy first and discard whatever overlaps it.
The complete version of a building survives, the cut ones do not.

Then each outline is regularized, because a traced raster boundary is a pixel
staircase, not a wall:

1. morphological closing dissolves zero-width pinch seams between body and porch
2. despiking drops doubled-back and near-duplicate vertices
3. orthogonalization snaps edges to the building's *own* dominant axis (a
   length-weighted 4-fold circular mean, refined to reject off-axis walls) and
   rebuilds true square corners by intersecting wall lines

Step 3 has guard rails. A building whose perimeter is less than 60% axis-aligned
— a church, a curved storefront — is only smoothed, never forced square. Any
result that moves more than 25% of the original area is thrown away and the
traced outline is kept. The `regularized` attribute records which happened:
`2` squared up, `1` smoothed only, `0` left as traced.

Finally, each polygon is linked to the largest touching neighbour of a
*different* material, which is how a wood porch finds its brick house
(`host_id`).

---

## Output

`output/<name>.geojson` and a matching `.shp/.shx/.dbf/.prj/.cpg` set, both in
WGS84 (EPSG:4326). Measurements are computed in a local metric frame, so areas
and lengths are in real metres.

| field | shapefile name | meaning |
| --- | --- | --- |
| `bldg_id` | `bldg_id` | sequential id, ordered north to south |
| `material` | `material` | `brick`, `frame`, `adobe`, `iron_fireproof`, `special` |
| `area_m2` | `area_m2` | footprint area, m² |
| `perim_m` | `perim_m` | perimeter, m |
| `bbox_w_m` / `bbox_h_m` | same | bounding box size, m |
| `azimuth_deg` | `azimuth` | dominant wall bearing, 0–90° |
| `regularized` | `regular` | 2 = orthogonalized, 1 = smoothed, 0 = as traced |
| `n_holes` | `n_holes` | interior courtyards kept (text-sized holes are dropped) |
| `n_sheets` | `n_sheets` | how many sheets this building was traced on |
| `edge_m` | `edge_m` | distance from centroid to its sheet's edge — low values mean the outline may still be clipped |
| `host_id` | `host_id` | `bldg_id` of the larger building this one is attached to, else 0 |
| `source_sheet` | `src_sheet` | filename of the sheet the surviving copy came from |

Shapefile field names are capped at 10 characters, hence the abbreviations. The
GeoJSON is the fuller record.

---

## Tuning for your own sheets

Everything lives in `config.py` and every value is also a command-line flag.

| knob | default | raise it to… |
| --- | --- | --- |
| `RES_M` | 0.10 | trade detail for speed (0.25–0.40 is fine for a trial) |
| `MIN_AREA_M2` | 6.0 | drop more small sheds and colour speckle |
| `MIN_SPLIT_M2` | 8.0 | stop splitting buildings into small material patches |
| `MIN_HOLE_M2` | 5.0 | treat more interior rings as text rather than courtyards |
| `DUP_MIN` / `DUP_IOU` | 0.50 | be stricter about calling two polygons the same building |
| `TOUCH_M` | 0.40 | link porches across wider gaps |
| `SIMPLIFY_M` | 0.30 | smooth the staircase harder before estimating bearing |

The geometry constants for orthogonalization (angle tolerance, wall snapping,
bevel detection) are at the top of `regularize.py` with a comment each.

If the material mix comes out wrong, the problem is almost always the colour
rules rather than the geometry. Run the `classify.py` preview on two or three
sheets, then adjust the hue and saturation cut-offs in `classify()`.

### Sanity checks worth running

- Total built area and median footprint size — the numbers above are a rough
  reference for a 1900s downtown.
- Count of `regularized == 0`. A high share means orthogonalization is bailing
  out, usually because tracing is noisy at your chosen resolution.
- Overlay the layer on a sheet raster in QGIS and look at a block or two. Nothing
  else catches georeferencing drift as fast.

---

## Known limitations

- **The map is the source, not the ground.** Sanborn surveyors generalized, and
  the sheets were revised by pasting patches over older ones. A footprint here is
  what the 1908 atlas asserted, not a survey.
- **Georeferencing error carries straight through.** Outlines are only as well
  placed as the sheet warp underneath them.
- **Attached rows split by material.** A brick block with a frame addition
  becomes two polygons linked by `host_id`, not one building. Dissolve on
  `host_id` if you want whole structures.
- **Printed text inside a building can survive as a hole** when it is larger
  than `MIN_HOLE_M2`.
- **Anything the key does not colour is invisible** — outbuildings drawn in
  outline only, and every non-building annotation, are absent by design.
- **Duplicate removal uses sheet bounding boxes.** Georeferenced scans are padded
  with nodata, so a building near the real edge of a sheet's *content* can still
  report a comfortable `edge_m`. Filter on `edge_m` if clipped outlines would
  hurt your analysis.

---

## Getting the sheets

The El Paso 1908 atlas used here comes from the University of Texas Libraries
GeoData repository, where each Sanborn sheet is catalogued as `utlmaps:<uuid>`
with a georeferenced GeoTIFF for download. The Library of Congress hosts the same
atlas unreferenced. Sheet rasters and pipeline outputs are deliberately not
committed to this repo — see `.gitignore`.

If you use this, cite the sheets as well as the code; the atlas is the data.

- UT Libraries GeoData: <https://geodata.lib.utexas.edu>
- Perry-Castañeda Map Collection, Sanborn maps of Texas:
  <https://maps.lib.utexas.edu/maps/sanborn/texas.html>
- Library of Congress, Sanborn maps: <https://www.loc.gov/collections/sanborn-maps/>

## Repo layout

```
config.py                 all paths and thresholds
classify.py               Sanborn colour key -> material labels (+ preview tool)
01_extract_sheets.py      step 1: trace one sheet at a time -> work/sheets/*.json
regularize.py             outline cleanup: despike, orthogonalize, smooth
02_build_footprints.py    step 2: dedupe, regularize, link, write the layer
requirements.txt
```

## Requirements

Python 3.9+, and `numpy`, `scipy`, `rasterio`, `shapely` (2.0 or newer), `pyshp`.
No GDAL command-line tools needed. Roughly 2 GB of RAM per sheet at 0.10 m/px.

## Publishing this repo

With [GitHub Desktop](https://desktop.github.com): **File > Add local
repository**, point it at this folder, let it create a repository, write a commit
message, **Commit to main**, then **Publish repository**.

Or from a terminal, with the [GitHub CLI](https://cli.github.com):

```bash
git init
git add .
git commit -m "Sanborn footprint extraction pipeline"
gh repo create sanborn-footprint-extraction --public --source=. --push
```

`.gitignore` already keeps sheet rasters, intermediates, and outputs out of the
commit, so the repo stays a few tens of kilobytes — check that the file list
before committing is only the source files, README, LICENSE, and
requirements.txt.

## License

MIT. See `LICENSE`.

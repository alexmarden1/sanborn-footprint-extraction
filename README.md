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
porches and wings are linked to the building they hang off. (Those figures are
from the original run; the pixel grid has since been corrected for latitude, so a
re-run shifts the counts by a few percent.)

It has also been run on the **1921 atlas of Austin, Texas** — 27 residential
sheets, 7,661 buildings, 96% wood frame — which needed different settings for the
same code, because its paper is cream-aged rather than white. Both are committed
as profiles.

So it is not El Paso-specific: any set of georeferenced sheets using the standard
Sanborn colour key should run through it. What a new atlas does need is ten
minutes of calibration, because the colour rules are relative to the paper. Start
with `00_calibrate.py` and [docs/CALIBRATING.md](docs/CALIBRATING.md).

---

## Quickstart

```bash
git clone https://github.com/alexmarden1/sanborn-footprint-extraction.git
cd sanborn-footprint-extraction

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put your georeferenced sheets (one GeoTIFF per sheet, any CRS) in `data/sheets/`
— see [docs/SHEETS.md](docs/SHEETS.md) for where to get them and the handful of
things that trip people up — then:

```bash
python 00_calibrate.py             # measure the atlas  -> calibration/*.png + advice
python 01_extract_sheets.py        # trace each sheet   -> work/sheets/*.json
python 02_build_footprints.py      # assemble the layer -> output/*.geojson, *.shp
```

**Step 0 is not optional on an atlas nobody has run before.** The colour rules
are relative to the paper, and a different printing or scanning run can hide a
whole material class or turn the paper itself into a building. `00_calibrate.py`
measures a sample of sheets, writes previews, and tells you what to change.
[docs/CALIBRATING.md](docs/CALIBRATING.md) walks through two real atlases, one
that needed nothing and one that needed three fixes.

If your atlas already has a profile, that's the whole run:

```bash
python 01_extract_sheets.py --profile profiles/el_paso_1908.json
python 02_build_footprints.py --profile profiles/el_paso_1908.json
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
the rules are auditable, and nobody has to label training data.

The rules are relative to each sheet's own paper, because that is the only way
they survive contact with a different scanning run. Paper tone is measured per
sheet — the median saturation of non-ink pixels, with its spread taken from the
lower tail so the fills themselves can't inflate it — and a colour fill has to
sit several spreads above it. White 1908 El Paso paper measures about 0.10 and
sets the bar near 0.14; cream-aged 1921 Austin paper measures about 0.29 and
raises it to 0.37. With a fixed threshold, that Austin paper reads as one
enormous adobe building covering half the sheet.

Saturation alone still isn't enough on a strongly tinted sheet, because a pale
wash can be *less* saturated than the paper under it: 1921 Austin's pink brick
measures 0.11 against tan paper at 0.20, so no floor can separate them. There,
hue does the work — the paper is tan at ~33°, brick is pink at ~350°, iron is
teal at ~185°. A fill therefore qualifies by being either more saturated than
the paper or clearly off its hue. On near-neutral paper the hue route switches
off, since the hue of unsaturated paper is noise.

Adobe needs a third rule, because it is a *neutral* wash: no more saturated
than aged paper, so saturation cannot find it. What separates them is
brightness — a wash is visibly darker than the paper around it, a paper tint is
not. Adobe is consequently the least reliable class, and the one to switch off
(`--classes`) for a city that never built in it.

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
| `CLASSES_USED` / `--classes` | all five | restrict to the materials your atlas uses, e.g. `brick,frame,special,iron_fireproof` |
| `SAT_K` (classify.py) | 3.0 | demand more saturation before calling something a fill — raise if paper leaks in, lower if pale washes are missed |
| `HUE_DELTA` (classify.py) | 25° | require a bigger hue difference from the paper before a pale wash counts as a fill |
| `PAPER_TINT_MIN` (classify.py) | 0.15 | paper saturation above which the hue route turns on at all |

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
- **Adobe is the weakest class.** It is a neutral wash separated from aged paper
  and grey line work only by being darker, so its share is the number to distrust
  first. On an atlas with no adobe, exclude it with `--classes`.
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
config.py                 paths, thresholds, profile loading
classify.py               Sanborn colour key -> material labels (+ preview tool)
sheetio.py                finding sheets and putting them on a metric grid
00_calibrate.py           step 0: measure a new atlas, recommend settings
01_extract_sheets.py      step 1: trace one sheet at a time -> work/sheets/*.json
regularize.py             outline cleanup: despike, orthogonalize, smooth
02_build_footprints.py    step 2: dedupe, regularize, link, write the layer
profiles/*.json           per-atlas settings, with notes on why they differ
docs/CALIBRATING.md       how to calibrate a new city, with two worked examples
requirements.txt

data/sheets/              your georeferenced sheet GeoTIFFs (see docs/SHEETS.md)
work/                     per-sheet intermediates (auto-created, resumable)
output/                   the finished GeoJSON + shapefile
calibration/              previews from step 0 (auto-created, disposable)
```

None of those four are committed — `data/` is yours entirely, and the other three
are created by the scripts — so cloning gives you the code without the gigabytes.
`work/` and `output/` keep a README each explaining what lands in them.

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

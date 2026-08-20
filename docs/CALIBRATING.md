# Calibrating for a new city

The colour rules in `classify.py` are not universal constants. Every Sanborn
atlas was printed, aged, and scanned differently, and the same pink that means
"brick" in one atlas can be paler than the *paper* of another. Calibration is
the ten minutes you spend finding out what your sheets actually look like, before
committing to a run of several hundred.

This guide uses the two atlases the pipeline has been run on, because they fail
in opposite directions and between them cover most of what you will hit.

---

## The loop

```bash
# 1. measure a sample of sheets
python 00_calibrate.py --sheet-dir data/sheets --sample 5

# 2. look at the previews it wrote
open calibration/*_labels.png        # next to the matching *_rgb.png

# 3. re-measure with the settings it suggested, until the previews look right
python 00_calibrate.py --sheet-dir data/sheets --sample 5 \
    --classes brick,frame --save-profile profiles/my_city.json

# 4. trial one sheet end to end
python 01_extract_sheets.py --profile profiles/my_city.json --limit 1 --res 0.25
python 02_build_footprints.py --name trial

# 5. overlay trial.geojson on a sheet in QGIS, then run the lot
python 01_extract_sheets.py --profile profiles/my_city.json
python 02_build_footprints.py --name my_city_footprints
```

Step 2 is the one people skip. Don't: the numbers can look reasonable while the
label image shows the entire sheet coloured in.

---

## Reading the report

```
=== utlmaps__sfi__austin_tx_1921__1k__x__84.tif
    CRS EPSG:3857 | native 12000 x 13000 px (~0.04 m/px) | ground 469 x 519 m
    paper: hue 33 deg, saturation 0.21 -> hue route ON (tinted paper)
    saturation floor 0.325 | classified as fill: 4.18% of sheet
    material         % of sheet  med hue  med sat  buildings
    brick                 0.01%      341     0.15          1
    frame                 4.14%       49     0.70        304
    ...
```

**CRS** — anything is fine; the pipeline reads the sheet in its own coordinates
and writes lon/lat. `MISSING` means the file is not georeferenced and cannot be
used. If `native` resolution is much coarser than your `--res`, you are
interpolating rather than reading detail.

**paper** — the tone the rules are measured against. Below saturation 0.15 the
paper is near-neutral and fills are found by saturation alone. Above it, the
paper is tinted, and the hue route switches on so that a wash paler than the
paper can still be recognised.

**classified as fill** — the share of the sheet the rules think is building. A
dense city block runs 10–20%. Thirty percent or more means paper is being read
as a material.

**buildings** — connected fills of at least 6 m². This is the number to trust
over pixel percentages: 300 buildings on a residential sheet is plausible, 555
is not.

**possible unrecognised washes** — coloured pixels clearly off the paper's hue
that the rules rejected anyway. Small values are ink fringing and scan edges.
Anything substantial means a material is being missed, which is the failure that
is easiest to overlook, because nothing in the output looks wrong; it is simply
absent.

**classes that might just be paper** — a class whose median hue matches the
paper's. This cannot be settled automatically, because a genuine neutral wash
(adobe, stone) also sits near the paper hue. Look at the preview.

---

## Worked example 1: El Paso 1908 — the easy case

```
paper: hue 36 deg, saturation 0.10 -> hue route off (neutral paper)
saturation floor 0.140 | classified as fill: 13.96% of sheet
brick   3.61%   hue   4   sat 0.21    75 buildings
frame   3.12%   hue  50   sat 0.34   193 buildings
adobe   7.23%   hue  38   sat 0.10   242 buildings
```

White paper, saturated fills, 14% fill on a dense sheet: nothing to change.
All five materials appear, and the defaults work.

The one caveat the report flags correctly is adobe, at the paper's own hue. It is
a neutral grey-brown wash found by being *darker* than the paper rather than more
saturated, which makes it the least reliable class in any atlas. In El Paso it is
genuinely common — it was a border city built in adobe — so it stays on, but its
7% is the number to distrust first.

## Worked example 2: Austin 1921 — the hard case

Same code, same defaults, and every one of the following went wrong.

**Failure 1: the grid.** The sheets are in EPSG:3857, so metres, not degrees.
The first run asked for a 240-million-pixel-wide array and died. Fixed in the
pipeline: pixel size now comes from the ground extent, so any CRS works. If you
see a `grid would be ... px` error, your sheet is either a whole-city mosaic or
its georeferencing is broken.

**Failure 2: the paper became a building.** Cream-aged paper at saturation 0.21
cleared the old fixed threshold of 0.13, and its hue (33°) sits in the adobe
band, so half of every sheet traced as one enormous adobe polygon. The report
makes this obvious:

```
classified as fill: 16.16% of sheet
adobe    11.98%   hue 34   sat 0.23   555 buildings   <- paper
CLASSES THAT MIGHT JUST BE PAPER:
  adobe   12.0% of a sheet, at the paper's own hue
```

555 "buildings" of adobe on a sheet with 30 visible houses, at the paper's exact
hue and saturation. Two fixes apply: the saturation floor is now measured per
sheet (0.34 here rather than a fixed 0.13), and Austin never built in adobe, so
`--classes brick,frame,special,iron_fireproof` removes the class entirely.

**Failure 3: the missing brick.** The first clean run came back 99.2% frame with
50 brick in 7,400 buildings — implausible for any city. The cause was subtle:
Austin's pink brick wash measures saturation **0.11, below the paper's own 0.21**,
so no saturation threshold could ever find it. What separates it from the paper
is hue: 350° against the paper's 33°. Hence the hue route, which is why the
report tells you whether it is on. After the fix, brick went 50 → 139, special
0 → 112, iron 7 → 75.

The lesson generalises: **on tinted paper, check for classes that are suspiciously
absent, not just classes that are suspiciously large.** The "possible
unrecognised washes" line exists for exactly this.

---

## Which knob for which symptom

| symptom | first thing to try |
| --- | --- |
| whole sheet traced as one class at the paper's hue | drop that class with `--classes`; if it is real, raise `SAT_K` |
| a material you can see is missing entirely | is the hue route on? if not, the paper is near-neutral and the fill is too pale — lower `--sat-k`; if on, lower `HUE_DELTA` |
| buildings traced smaller than they look | lower `--sat-k`; the floor is eating the pale edge of each fill |
| thin slivers along streets and text | raise `HUE_ROUTE_S_MIN`, or raise `MIN_AREA_M2` |
| `grid would be N x M px` error | a mosaic or an index plate is in the sheet folder — narrow `--pattern` or add to `skip_sheets` |
| plausible numbers, wrong places | georeferencing, not classification — check the sheet in QGIS |

## Saving what you learn

`--save-profile profiles/my_city.json` records the measured paper values, the CRS,
and the settings, so the run is reproducible from one flag:

```bash
python 01_extract_sheets.py --profile profiles/my_city.json
```

Committed profiles double as documentation. `profiles/el_paso_1908.json` and
`profiles/austin_1921.json` are the two above; both carry a `notes` field
explaining why their settings differ, which is more useful to the next person
than the settings themselves. If you calibrate a new atlas, a pull request adding
its profile is very welcome.

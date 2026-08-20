# data/sheets — put your Sanborn sheets here

One **georeferenced GeoTIFF per sheet**, in EPSG:4326 (WGS84 lat/lon). Filenames
don't matter; the pipeline globs `*.tif` and records whatever name it finds in the
`source_sheet` attribute.

```
data/sheets/
  utlmaps_057d0e4a-....tif
  utlmaps_0621ee87-....tif
  ...
```

Nothing in this folder is committed to git — see the repo's `.gitignore`.

## Things that will trip you up

- **Only sheets belong here.** A city-wide mosaic or an index plate also matches
  `*.tif`, and a mosaic will exhaust memory when the pipeline tries to read it as
  one sheet. Either keep it elsewhere, narrow the match with
  `--pattern 'utlmaps_*.tif'`, or add part of its filename to `SKIP_SHEETS` in
  `config.py`.
- **Non-map plates trace into noise.** Index and key sheets have no building
  colours to find; list them in `SKIP_SHEETS` too.
- **Sheets must already be georeferenced.** This pipeline reads the raster's own
  coordinates and never warps anything. Un-referenced scans come out as
  polygons in the wrong hemisphere.
- **Padded scans are normal.** Georeferenced sheets are usually rotated inside a
  larger nodata frame; blank margins are skipped automatically.

## Keeping the sheets somewhere else

They're big, so you may not want them inside the repo at all. Point the pipeline
at wherever they live instead:

```bash
python 01_extract_sheets.py --sheet-dir /path/to/sheets
# or, once per shell:
SANBORN_SHEET_DIR=/path/to/sheets python 01_extract_sheets.py
```

Where to get sheets for the El Paso 1908 atlas this was built on: see
[Getting the sheets](../../README.md#getting-the-sheets).

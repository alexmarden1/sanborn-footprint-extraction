# Preparing your sheets

The pipeline reads **one georeferenced GeoTIFF per Sanborn sheet**. Any CRS works
— lat/lon (EPSG:4326), Web Mercator, UTM, state plane — because pixel size is
derived from each sheet's ground extent and the traced polygons are converted to
lon/lat automatically. Filenames don't matter; whatever name a sheet has is
recorded in the `source_sheet` attribute.

The default location is `data/sheets/`, which you create yourself:

```bash
mkdir -p data/sheets
```

Nothing under `data/` is committed — see `.gitignore`. Sheets are large, so you
may well prefer to keep them outside the repo entirely and point the pipeline at
them:

```bash
python 01_extract_sheets.py --sheet-dir /path/to/sheets
# or, once per shell:
SANBORN_SHEET_DIR=/path/to/sheets python 01_extract_sheets.py
```

A profile can also carry the location, though committed profiles deliberately
leave it out so they don't hardcode one machine's paths.

## Things that will trip you up

- **Only sheets belong in the folder.** A city-wide mosaic or an index plate also
  matches `*.tif`, and a mosaic will exhaust memory when the pipeline tries to
  read it as a single sheet. Either keep it elsewhere, narrow the match with
  `--pattern 'utlmaps_*.tif'`, or add part of its filename to `SKIP_SHEETS` in
  `config.py` (or `skip_sheets` in a profile).
- **Non-map plates trace into noise.** Index and key sheets have no building
  colours to find; list them in `skip_sheets` too.
- **Sheets must already be georeferenced**, though any CRS is fine. A raster with
  no CRS is reported as a failure and skipped rather than traced into nonsense.
- **Metadata is not imagery.** Catalogue records (GeoBlacklight JSON, ISO XML)
  download separately from the rasters and are easy to mistake for the data. Each
  record contains the download URL for its sheet.
- **Padded scans are normal.** Georeferenced sheets are usually rotated inside a
  larger nodata frame; blank margins are skipped automatically. One consequence:
  `edge_m` in the output measures distance to the sheet's *bounding box*, not to
  the edge of the mapped content.
- **Cloud-synced folders need care.** OneDrive, Box, and Dropbox present files as
  placeholders, and GDAL will stall or error when a read forces a download
  mid-stream. Mark the folder "available offline", or copy the sheets to local
  disk before running.

## Where to get sheets

The two atlases this pipeline has been run on both come from the University of
Texas Libraries GeoData repository, where each sheet is catalogued with a
georeferenced GeoTIFF for download. The Library of Congress hosts many of the
same atlases unreferenced.

- UT Libraries GeoData: <https://geodata.lib.utexas.edu>
- Perry-Castañeda Map Collection, Sanborn maps of Texas:
  <https://maps.lib.utexas.edu/maps/sanborn/texas.html>
- Library of Congress, Sanborn maps: <https://www.loc.gov/collections/sanborn-maps/>

Cite the sheets as well as the code; the atlas is the data.

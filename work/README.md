# work — intermediates from step 1

`01_extract_sheets.py` writes one JSON of traced polygons per sheet into
`work/sheets/`, and `02_build_footprints.py` reads only those files. Created
automatically; nothing here is committed.

Two useful consequences:

- **Step 1 resumes.** Sheets that already have a JSON are skipped, so an
  interrupted run picks up where it stopped. Force a re-trace with `--overwrite`,
  or just delete the file for that sheet.
- **Step 2 is cheap to re-run.** It never touches the rasters, so re-assembling
  the layer with different dedupe or regularization thresholds takes seconds.

Delete this folder any time — it costs one step-1 run to rebuild. If you change
`--res`, delete it first, or you'll assemble a mix of resolutions.

Override the location with `--work-dir /path` or `SANBORN_WORK_DIR=/path`.

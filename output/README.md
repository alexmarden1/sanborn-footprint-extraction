# output — the finished layer

`02_build_footprints.py` writes the footprint layer here, as GeoJSON plus a
matching shapefile set, both in WGS84 (EPSG:4326):

```
output/
  sanborn_footprints.geojson
  sanborn_footprints.shp   .shx   .dbf   .prj   .cpg
```

Created automatically; nothing here is committed, so publishing your results is a
deliberate act rather than an accident. See
[Output](../README.md#output) for what each attribute means.

Name the layer something else with `--name my_city_1908`, or send it elsewhere
with `--out-dir /path` or `SANBORN_OUT_DIR=/path`. Re-running overwrites a layer
of the same name without warning.

# earthaccess-sliderule

On-demand processing of ICESat-2 and GEDI data with
[SlideRule](https://slideruleearth.io/), through earthaccess.

## What SlideRule does

SlideRule is a cloud computing platform for satellite altimetry and lidar data.
Instead of downloading ATL03/ATL06/ATL08 or GEDI granules and processing them
locally, you send SlideRule a processing request — an API such as `atl06p`,
`atl03p`, `atl08p`, `atl13p` or a GEDI processor, plus parameters and a region
of interest — and it runs the analysis server-side over the granules you name,
returning derived, geolocated point data as a `GeoDataFrame`.

Because the processing happens remotely and is scoped to a region and set of
resources, SlideRule is a good fit for large-area or on-the-fly analysis where
downloading raw granules would be wasteful.

## Install

```bash
pip install earthaccess-sliderule
```

## Use with earthaccess

```python
import earthaccess as ea

results = ea.search_data(
    short_name="ATL06",
    bounding_box=(-120, 40, -110, 45),
    temporal=("2020-01-01", "2020-02-01"),
)

df = ea.plugins.sliderule.request(
    results,
    api="atl06p",
    parms={"pass_invalid": False},
    aoi={"type": "Polygon", "coordinates": [[[...]]]},
).results()
# df is a geopandas.GeoDataFrame of geolocated points
```

The searched granules scope the request automatically (their granule URs are
sent as `resources`). You can also run from an area of interest and parameters
alone, with no search results — SlideRule does not need them:

```python
df = ea.plugins.sliderule.request(
    api="atl06p",
    parms={...},
    aoi={"type": "Polygon", "coordinates": [[[...]]]},
).results()
```

Or work with the job explicitly:

```python
job = ea.plugins.sliderule.request(results, api="atl06p", parms={...})
df = job.results()          # blocks until the processing finishes
```

Because the result is already a usable `GeoDataFrame`, `ea.open(job)` returns it
as-is.

### Options

| Option | Meaning |
| --- | --- |
| `api` | required; the processor, e.g. `"atl06p"`, `"atl03p"`, `"atl08p"` |
| `parms` | processor parameters (plain dict, passed through) |
| `aoi` | GeoJSON region of interest |
| `resources` | explicit granule names; defaults to the searched granules |

## Authentication and configuration

SlideRule manages its own credentials, so this plugin does not use your
Earthdata Login session. Configure the endpoint and organization before the
first call:

```python
ea.plugins.sliderule.configure(
    url="https://slideruleearth.io",
    organization="my-org",
)
```

## Checking that a collection is supported

SlideRule supports a small set of datasets (ICESat-2 and GEDI). The plugin
rejects anything else early and lists the processors it can run:

```python
ea.plugins.sliderule.capabilities("ATL06")
# sliderule supported ATL06
#   process          run an on-demand processor selected by api
#   subset_bbox      restrict the result to a spatial extent (bbox or wkt)
#   subset_temporal  restrict the result to a time interval (temporal)
#   formats: geoparquet
#   apis: atl03sp, atl03vp, atl03x, atl06p, atl06sp, atl08p, atl13sp, atl24v,
#         gedil1b, gedil2a, gedil4a
#   hint: use .explain() for options and examples

ea.plugins.sliderule.request(landsat_results, api="atl06p").results()
# UnsupportedCollectionError: sliderule does not support LANDSAT_8_C1 (...):
#   LANDSAT_8_C1 is not a SlideRule dataset. ...
```

## License

MIT.

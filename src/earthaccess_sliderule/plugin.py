"""The SlideRule plugin: on-demand processing with its own credentials.

SlideRule manages its own authentication, so this plugin never touches
``ctx.auth``. The client keeps global state, so every call is serialized with a
lock and ``init`` runs once, inside the worker.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from earthaccess_plugin_spec import Capabilities, Capability, PluginBase
from earthaccess_plugin_spec.granules import (
    collection_label,
    native_id,
    related_urls,
)

__all__ = ["SlideRulePlugin"]

# SlideRule keys datasets off the CMR short name. Most match exactly; GEDI L4A
# collections are versioned (e.g. GEDI_L4A_AGB_Density_V2_1_2056), so prefix
# match those. "ATL03_ANC_MASKS" and friends are deliberately excluded by the
# exact-match ATL entries.
_SUPPORTED_SHORT_NAMES = frozenset(
    {
        # ICESat-2
        "ATL03",
        "ATL06",
        "ATL08",
        "ATL09",
        "ATL13",
        "ATL24",
        # GEDI
        "GEDI01_B",
        "GEDI02_A",
        "GEDI02_B",
    }
)
_SUPPORTED_PREFIXES = ("GEDI_L4A",)
_HDF_EXTENSIONS = (".h5", ".hdf5")
# SlideRule resource filenames look like ATL03_...h5 / GEDI04_A_...h5. The
# prefixed GEDI GranuleUR (GEDI_L4A_AGB_Density_V2_1.GEDI04_A_...h5) does not.
_RESOURCE_NAME_RE = re.compile(r"^(ATL|GEDI)\d")

# Server-side APIs (from the sliderule client): ICESat-2 and GEDI processors.
_SLIDERULE_APIS = frozenset(
    {
        "atl03sp",
        "atl03vp",
        "atl03x",
        "atl06p",
        "atl06sp",
        "atl08p",
        "atl13sp",
        "atl24v",
        "gedil1b",
        "gedil2a",
        "gedil4a",
    }
)
_SLIDERULE_OPERATIONS = frozenset(
    {
        Capability.PROCESS,
        Capability.SUBSET_BBOX,
        Capability.SUBSET_TEMPORAL,
    }
)
_SLIDERULE_FORMATS = frozenset({"geoparquet"})
# SlideRule expresses time bounds in its processor parameters.
_TEMPORAL_PARMS = ("t0", "t1")


class SlideRulePlugin(PluginBase):
    name = "sliderule"
    needs = frozenset({"native_id"})
    requires_results = False  # can run from aoi + parms alone
    auth = "own"  # SlideRule manages its own credentials
    output = "dataframe"

    @dataclass
    class Options:
        api: str  # required, for example 'atl06p'
        parms: dict = field(default_factory=dict)
        aoi: Any = None  # GeoJSON dict, file path, or GeoDataFrame
        resources: list[str] | None = None  # overrides results.native_ids
        # Canonical capability options; translated to aoi/parms below.
        bbox: tuple[float, float, float, float] | None = None  # west, south, east, north
        temporal: Mapping[str, Any] | Sequence[Any] | None = None

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._init_kwargs: dict[str, Any] = {}
        self._ready = False
        self._lock = threading.Lock()

    def configure(self, **init_kwargs: Any) -> None:
        """Set ``sliderule.init`` keyword arguments (url, organization, ...)."""

        self._init_kwargs = init_kwargs
        self._ready = False

    def _request(self, results: Any, options: Options) -> Any:
        resources = options.resources
        if resources is None and results is not None:
            resources = _resource_names(results)

        aoi = options.aoi
        if options.bbox is not None:
            if aoi is not None:
                raise TypeError("pass either bbox or aoi, not both")
            aoi = _bbox_polygon(options.bbox)

        # SlideRule's ICESat-2 processing endpoints need an asset to locate the
        # data (``sliderule.icesat2`` defaults it to this); ``sliderule.run``
        # does not set one, and without it the server accepts the request but
        # produces no output.
        parms = dict(options.parms)
        if options.temporal is not None:
            start, stop = _temporal_bounds(options.temporal)
            clash = sorted(set(_TEMPORAL_PARMS) & set(parms))
            if clash:
                raise TypeError(
                    f"pass either temporal or parms[{clash}], not both"
                )
            parms[_TEMPORAL_PARMS[0]], parms[_TEMPORAL_PARMS[1]] = start, stop
        if "asset" not in parms and options.api.lower().startswith("atl"):
            parms["asset"] = "icesat2"

        def call() -> Any:
            import sliderule

            region, cleanup = _as_region_file(aoi)
            try:
                with self._lock:
                    if not self._ready:
                        sliderule.init(**self._init_kwargs)
                        self._ready = True
                    return sliderule.run(
                        options.api, parms, aoi=region, resources=resources
                    )
            finally:
                if cleanup and os.path.exists(cleanup):
                    os.remove(cleanup)

        return self.job(call)

    def _supports(self, results: Any) -> Capabilities:
        short_name = (getattr(results, "short_name", "") or "").upper()
        supported = short_name in _SUPPORTED_SHORT_NAMES or any(
            short_name.startswith(prefix) for prefix in _SUPPORTED_PREFIXES
        )
        if supported:
            return Capabilities(
                True,
                operations=_SLIDERULE_OPERATIONS,
                apis=_SLIDERULE_APIS,
                formats=_SLIDERULE_FORMATS,
            )
        label = collection_label(results) or "these results"
        return Capabilities(False, reason=f"{label} is not a SlideRule dataset")


def _bbox_polygon(
    bbox: Sequence[float],
) -> dict[str, Any]:
    """A closed GeoJSON polygon for a (west, south, east, north) bbox."""

    west, south, east, north = bbox
    ring = [
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _temporal_bounds(temporal: Mapping[str, Any] | Sequence[Any]) -> tuple[str, str]:
    """Normalise ``temporal`` to the two ISO-8601 bounds SlideRule expects."""

    if isinstance(temporal, Mapping):
        start, stop = temporal["start"], temporal["stop"]
    else:
        start, stop = temporal
    return _isoformat(start), _isoformat(stop)


def _isoformat(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _resource_names(results: Any) -> list[str]:
    """SlideRule resource names: the HDF filenames, not the CMR ``GranuleUR``.

    For GEDI the ``GranuleUR`` is prefixed with the collection short name
    (``GEDI_L4A_....GEDI04_A_....h5``); SlideRule wants the bare filename, which
    is available on the granules' data links. Streams the resultset, so only the
    names are retained. Falls back to the granule URs.
    """

    names: list[str] = []
    fallbacks: list[str] = []
    for granule in results:
        ur = native_id(granule)
        if ur and ur not in fallbacks:
            fallbacks.append(ur)
        for related in related_urls(granule):
            url = (related.get("URL") or "").split("?")[0].rstrip("/")
            base = url.rsplit("/", 1)[-1]
            if (
                base.endswith(_HDF_EXTENSIONS)
                and _RESOURCE_NAME_RE.match(base)
                and base not in names
            ):
                names.append(base)
    return names or fallbacks


def _as_region_file(aoi: Any) -> tuple[Any, str | None]:
    """Normalise ``aoi`` to something ``sliderule.run`` accepts.

    ``sliderule.run`` wants a file path (its own ``aoi != None`` check trips on a
    DataFrame/dict), so a GeoJSON dict or GeoDataFrame is written to a temp file.
    Returns ``(source, path_to_delete)``; a path or ``None`` passes through.
    """

    if aoi is None or isinstance(aoi, str):
        return aoi, None

    path = tempfile.mktemp(suffix=".geojson")
    to_file = getattr(aoi, "to_file", None)
    if callable(to_file):  # GeoDataFrame
        to_file(path, driver="GeoJSON")
        return path, path

    geometry = aoi
    if isinstance(aoi, dict) and aoi.get("type") == "Polygon":
        geometry = {"type": "Feature", "properties": {}, "geometry": aoi}
    with open(path, "w") as handle:
        json.dump(geometry, handle)
    return path, path

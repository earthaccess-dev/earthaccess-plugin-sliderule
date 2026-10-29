"""Conformance fixtures for SlideRule: a fake ``sliderule`` module."""

from __future__ import annotations

import json
import os
import sys
import types
from contextlib import AbstractContextManager
from typing import Any
from unittest import mock

from earthaccess_plugin_spec import Capability
from earthaccess_plugin_spec.results import GranuleResultSet
from earthaccess_plugin_spec.testing import Fixtures

__all__ = ["FakeSlideRule", "SlideRuleFixtures", "make_granule"]


def make_granule(
    *,
    collection_id: str = "C-ATL06",
    concept_id: str = "G1",
    ur: str = "G1.EXAMPLE",
    short_name: str = "ATL06",
    version: str = "006",
) -> dict[str, Any]:
    return {
        "meta": {"concept-id": concept_id, "collection-concept-id": collection_id},
        "umm": {
            "GranuleUR": ur,
            "CollectionReference": {"ShortName": short_name, "Version": version},
        },
    }


class FakeSlideRule:
    """Stand-in for the global-state ``sliderule`` module."""

    def __init__(self) -> None:
        self.inits: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    def init(self, **kwargs: Any) -> None:
        self.inits.append(kwargs)

    def run(
        self,
        api: str,
        parms: dict[str, Any],
        aoi: dict | None = None,
        resources: list[str] | None = None,
    ) -> dict[str, Any]:
        geometry = None
        if isinstance(aoi, str) and os.path.exists(aoi):
            with open(aoi) as handle:
                geometry = json.load(handle)
        self.runs.append(
            {
                "api": api,
                "parms": parms,
                "aoi": aoi,
                "aoi_geojson": geometry,
                "resources": resources,
            }
        )
        return {"api": api, "count": len(resources or [])}


def _module_for(backend: FakeSlideRule) -> types.ModuleType:
    module = types.ModuleType("sliderule")
    module.init = backend.init  # type: ignore[attr-defined]
    module.run = backend.run  # type: ignore[attr-defined]
    return module


class SlideRuleFixtures(Fixtures):
    def __init__(self, backend: FakeSlideRule | None = None, **kwargs: Any) -> None:
        kwargs.setdefault(
            "options",
            {
                "api": "atl06p",
                "parms": {"pass_invalid": False},
                "aoi": {"type": "FeatureCollection", "features": []},
            },
        )
        kwargs.setdefault(
            "result_set", GranuleResultSet([make_granule(collection_id="C-ATL06")])
        )
        kwargs.setdefault(
            "unsupported_result_set",
            GranuleResultSet(
                [
                    make_granule(
                        collection_id="C-LANDSAT",
                        short_name="LANDSAT_8_C1",
                    )
                ]
            ),
        )
        kwargs.setdefault(
            "capability_cases",
            {
                Capability.SUBSET_BBOX: {
                    "api": "atl06p",
                    "bbox": (-105.0, 39.0, -104.0, 40.0),
                },
                Capability.SUBSET_TEMPORAL: {
                    "api": "atl06p",
                    "temporal": {
                        "start": "2020-01-01T00:00:00",
                        "stop": "2020-01-02T00:00:00",
                    },
                },
                Capability.PROCESS: {"api": "atl06p"},
            },
        )
        super().__init__(**kwargs)
        self.backend_impl = backend or FakeSlideRule()
        self.capability_assertions.setdefault(
            Capability.SUBSET_BBOX, self._assert_bbox
        )
        self.capability_assertions.setdefault(
            Capability.SUBSET_TEMPORAL, self._assert_temporal
        )
        self.capability_assertions.setdefault(
            Capability.PROCESS, self._assert_process
        )

    def _last_run(self) -> dict[str, Any]:
        assert self.backend_impl.runs, "the backend was never called"
        return self.backend_impl.runs[-1]

    def _assert_bbox(self, job: Any) -> None:
        geometry = self._last_run()["aoi_geojson"]
        if geometry.get("type") == "Feature":
            geometry = geometry["geometry"]
        assert geometry["type"] == "Polygon", geometry
        ring = geometry["coordinates"][0]
        assert ring[0] == ring[-1], "the polygon ring must be closed"
        assert tuple(ring[0]) == (-105.0, 39.0), ring

    def _assert_temporal(self, job: Any) -> None:
        parms = self._last_run()["parms"]
        assert parms["t0"] == "2020-01-01T00:00:00", parms
        assert parms["t1"] == "2020-01-02T00:00:00", parms

    def _assert_process(self, job: Any) -> None:
        assert self._last_run()["api"] == "atl06p", self._last_run()

    def backend(self) -> AbstractContextManager[Any]:
        return mock.patch.dict(
            sys.modules, {"sliderule": _module_for(self.backend_impl)}
        )

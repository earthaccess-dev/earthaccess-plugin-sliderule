import pytest
from earthaccess_plugin_spec import Capability, UnsupportedCollectionError
from earthaccess_plugin_spec.results import GranuleResultSet
from earthaccess_plugin_spec.testing import fake_context, sample_result_set

from earthaccess_sliderule import SlideRulePlugin
from earthaccess_sliderule.plugin import _SLIDERULE_APIS

from .fixtures import FakeSlideRule, SlideRuleFixtures, make_granule


class TestOptions:
    def test_api_required(self):
        with pytest.raises(TypeError):
            SlideRulePlugin.Options()


class TestRequest:
    def test_resources_from_results(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            result = plugin.request(fixtures.result_set, **fixtures.options).results()
        assert result["count"] == 1
        assert backend.runs[0]["resources"] == list(fixtures.result_set.native_ids)
        assert backend.runs[0]["api"] == "atl06p"

    def test_resources_override(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            plugin.request(
                fixtures.result_set,
                api="atl06p",
                parms={},
                resources=["custom-1", "custom-2"],
            ).results()
        assert backend.runs[0]["resources"] == ["custom-1", "custom-2"]

    def test_defaults_icesat2_asset(self):
        # Regression: without an asset, SlideRule's processing endpoints
        # (atl06p, ...) accept the request but return no output. Its own
        # icesat2 helpers default the asset; sliderule.run does not.
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            plugin.request(
                fixtures.result_set,
                api="atl06p",
                parms={},
                resources=["custom-1"],
            ).results()
        assert backend.runs[0]["parms"]["asset"] == "icesat2"

    def test_explicit_asset_is_preserved(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            plugin.request(
                fixtures.result_set,
                api="atl06p",
                parms={"asset": "my-asset"},
                resources=["custom-1"],
            ).results()
        assert backend.runs[0]["parms"]["asset"] == "my-asset"

    def test_no_default_asset_for_non_icesat2_api(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            plugin.request(
                fixtures.result_set,
                api="gedil4a",
                parms={"asset": "ornldaac-s3"},
                resources=["custom-1"],
            ).results()
        assert backend.runs[0]["parms"] == {"asset": "ornldaac-s3"}

    def test_requires_no_results(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            result = plugin.request(
                None,
                api="atl06p",
                parms={},
                aoi={"type": "Point", "coordinates": [0, 0]},
            ).results()
        assert result["count"] == 0
        assert backend.runs[0]["resources"] is None

    def test_init_runs_once(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        plugin.configure(url="https://sliderule.example", organization="org")
        with fixtures.backend():
            plugin.request(fixtures.result_set, **fixtures.options).results()
            plugin.request(fixtures.result_set, **fixtures.options).results()
        assert backend.inits == [
            {"url": "https://sliderule.example", "organization": "org"}
        ]
        assert len(backend.runs) == 2

    def test_configure_resets_readiness(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        with fixtures.backend():
            plugin.request(fixtures.result_set, **fixtures.options).results()
            plugin.configure(url="https://other.example")
            plugin.request(fixtures.result_set, **fixtures.options).results()
        assert len(backend.inits) == 2


def test_entry_point_registered():
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="earthaccess.plugins")}
    assert "sliderule" in names


def test_sample_result_set_has_native_ids():
    assert sample_result_set().native_ids


class TestSupport:
    @pytest.mark.parametrize("short_name", ["ATL06", "atl06", "GEDI02_A"])
    def test_allowlisted(self, short_name):
        plugin = SlideRulePlugin(fake_context())
        results = GranuleResultSet([make_granule(short_name=short_name)])
        capabilities = plugin.capabilities(results)
        assert capabilities.ok is True
        assert capabilities.operations == {
            Capability.PROCESS,
            Capability.SUBSET_BBOX,
            Capability.SUBSET_TEMPORAL,
        }
        assert capabilities.apis == _SLIDERULE_APIS
        assert "geoparquet" in capabilities.formats

    def test_capabilities_by_short_name(self):
        plugin = SlideRulePlugin(fake_context())
        capabilities = plugin.capabilities("ATL03")
        assert capabilities.ok is True
        assert capabilities.collection == "ATL03"

    def test_versioned_gedi_l4a_is_supported(self):
        plugin = SlideRulePlugin(fake_context())
        results = GranuleResultSet(
            [make_granule(short_name="GEDI_L4A_AGB_Density_V2_1_2056")]
        )
        assert plugin.capabilities(results).ok is True

    def test_rejects_landsat(self):
        plugin = SlideRulePlugin(fake_context())
        results = GranuleResultSet(
            [make_granule(collection_id="C-LANDSAT", short_name="LANDSAT_8_C1")]
        )
        support = plugin.capabilities(results)
        assert support.ok is False
        assert "LANDSAT_8_C1" in support.reason

    def test_request_raises_before_backend(self):
        backend = FakeSlideRule()
        fixtures = SlideRuleFixtures(backend=backend)
        plugin = SlideRulePlugin(fake_context())
        results = GranuleResultSet(
            [make_granule(collection_id="C-LANDSAT", short_name="LANDSAT_8_C1")]
        )
        with fixtures.backend(), pytest.raises(UnsupportedCollectionError):
            plugin.request(results, api="atl06p")
        assert backend.runs == []


class TestResourceNames:
    def test_uses_hdf_filenames_from_related_urls(self):
        # GEDI GranuleUR is prefixed; SlideRule wants the bare filename.
        granule = make_granule(ur="GEDI_L4A_AGB_Density_V2_1.GEDI04_A_2019.h5")
        granule["umm"]["RelatedUrls"] = [
            {"Type": "GET DATA", "URL": "https://e.org/GEDI04_A_2019.h5"},
            {"Type": "GET DATA", "URL": "https://e.org/GEDI04_A_2019.h5?token=1"},
            # The prefixed GranuleUR variant must be ignored.
            {
                "Type": "GET DATA",
                "URL": "https://e.org/GEDI_L4A_AGB_Density_V2_1.GEDI04_A_2019.h5",
            },
            {"Type": "EXTENDED METADATA", "URL": "https://e.org/GEDI04_A_2019.xml"},
        ]
        from earthaccess_sliderule.plugin import _resource_names

        assert _resource_names(GranuleResultSet([granule])) == ["GEDI04_A_2019.h5"]

    def test_falls_back_to_native_ids(self):
        from earthaccess_sliderule.plugin import _resource_names

        granule = make_granule(ur="ATL03_20230602091058_11151903_007_01.h5")
        assert _resource_names(GranuleResultSet([granule])) == [
            "ATL03_20230602091058_11151903_007_01.h5"
        ]

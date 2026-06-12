import tempfile

import pytest

from lifeengine.constants import PLUGIN_VERSION
from lifeengine.runtime import LifeEngineRuntime


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="le_v0126_")
    monkeypatch.setenv("HERMES_HOME", d)
    yield d


def test_v0126_collection_requires_visual_reference_and_lazy_generates():
    assert PLUGIN_VERSION == "0.12.6"
    rt = LifeEngineRuntime()
    try:
        item_out = rt.collection("add_item", collection_type="wardrobe", name="白色短上衣", description="轻薄棉混纺")
        item = item_out["item"]
        assert item["assets"]
        assert all(a["status"] == "pending_generation" for a in item["assets"])

        checkout = rt.collection("checkout", item_id=item["id"])
        assert checkout["ok"] is False
        assert checkout["error"] == "visual_reference_required"
        assert "不能只按文字描述使用" in checkout["rendered"]

        outfit = rt.collection("outfit")
        assert outfit["ok"] is False
        assert outfit["outfit_plan"]["status"] == "waiting_assets"
        assert outfit["missing_visual_references"]
        assert outfit["outfit_plan"]["reasoning"]["lazy_generate_missing_assets"] is True
        assert "禁止只按文字描述重建物品" in outfit["rendered"]
    finally:
        rt.close()


def test_v0126_collection_uses_asset_uri_as_reference():
    rt = LifeEngineRuntime()
    try:
        item = rt.collection("add_item", collection_type="wardrobe", name="蓝色外套")["item"]
        asset = item["assets"][0]
        uri = "/root/.hermes/assets/wardrobe/blue-coat-front.png"
        bound = rt.collection("set_asset", asset_id=asset["id"], asset_uri=uri)
        assert bound["ok"] is True

        checkout = rt.collection("checkout", item_id=item["id"])
        assert checkout["ok"] is True
        assert checkout["reference_assets"][0]["asset_uri"] == uri

        rt.collection("return", item_id=item["id"], cleanliness_state="clean")
        outfit = rt.collection("outfit")
        assert outfit["ok"] is True
        assert outfit["reference_assets"]["wardrobe"][0]["asset_uri"] == uri
        assert uri in outfit["rendered"]
    finally:
        rt.close()

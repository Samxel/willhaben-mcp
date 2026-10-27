"""Failures reach the model as is_error results that say what went wrong."""

import httpx
import pytest

from conftest import ToolFailed
from samples import detail

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("tool, args, message", [
    ("search_willhaben", {}, "Provide 'keyword'"),
    ("search_willhaben", {"keyword": "x", "rows": 0}, "rows must be at least 1"),
    ("search_willhaben", {"keyword": "x", "category": "Gibtsnicht"}, "Unknown category"),
    ("search_willhaben", {"keyword": "x", "clothing_size": "XXXL"}, "Unknown clothing size"),
    ("search_autos", {"price_from": 5000, "price_to": 100}, "price_from .* is above price_to"),
    ("search_autos", {"make": "Gibtsnicht"}, "Unknown make"),
    ("search_autos", {"model": "Golf"}, "also pass 'make'"),
    ("search_immobilien", {"property_type": "Schloss"}, "Unknown property type"),
    ("search_immobilien", {"rooms": "12"}, "Unknown rooms value"),
])
async def test_bad_arguments_explain_themselves(wh, call, tool, args, message):
    with pytest.raises(ToolFailed, match=message):
        await call(tool, **args)


async def test_upstream_failures_are_phrased(wh, call):
    wh.on("publicapi.willhaben.at", "/atdetail/v1/1", httpx.Response(404))
    with pytest.raises(ToolFailed, match="no ad found"):
        await call("get_ad_detail", ad_id="1")
    with pytest.raises(ToolFailed, match="no ad found"):
        await call("get_ad_images", ad_id="1")
    wh.on("app-aggregator.willhaben.at", "/restapi/v2/search/atz/3/2", httpx.Response(503))
    with pytest.raises(ToolFailed, match="failed on the car model request"):
        await call("list_car_models", make="BMW")


async def test_a_rotated_token_key_is_named(wh, call):
    wh.on("app-aggregator.willhaben.at", "/api/v1/application-data", httpx.Response(401))
    with pytest.raises(ToolFailed, match="signing key was most likely rotated"):
        await call("get_ad_detail", ad_id="1")


async def test_an_expired_token_is_refreshed_once(wh, call):
    tokens = iter(["old", "new"])
    wh.on("app-aggregator.willhaben.at", "/api/v1/application-data",
          lambda request: {"applicationToken": {"value": next(tokens)}})
    wh.on("publicapi.willhaben.at", "/atdetail/v1/7",
          lambda request: (detail(7, "iPhone 13") if request.headers["x-wh-application-token"] == "new"
                           else httpx.Response(401)))
    assert (await call("get_ad_detail", ad_id="7"))["title"] == "iPhone 13"


async def test_an_ad_without_a_seller_id_says_so(wh, call):
    raw = detail(7, "iPhone 13", seller_id=None)
    wh.on("publicapi.willhaben.at", "/atdetail/v1/7", raw)
    with pytest.raises(ToolFailed, match="names no seller id"):
        await call("get_ad_seller", ad_id="7")

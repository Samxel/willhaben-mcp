"""Smoke tests against the real API. The app API, the token request and the
param sets are reverse-engineered, so these are what notices willhaben changing
them; they only run with WILLHABEN_LIVE=1 (the scheduled CI job sets it)."""

import os

import httpx
import pytest

import main

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("WILLHABEN_LIVE"), reason="set WILLHABEN_LIVE=1 to hit the real API"),
]


@pytest.fixture(autouse=True)
async def fresh_client(monkeypatch):
    """main.client binds to the first event loop it runs on, and every test
    gets its own loop; so does the token lock."""
    client = httpx.AsyncClient(http2=True)
    monkeypatch.setattr(main, "client", client)
    monkeypatch.setattr(main, "_app_token_lock", main.asyncio.Lock())
    yield
    await client.aclose()


async def test_marketplace_search_detail_and_seller(call):
    result = await call("search_willhaben", keyword="iPhone 13", category=2722, rows=3)
    assert result["rows_found"] > 0 and result["results"]
    ad_id = result["results"][0]["id"]
    detail = await call("get_ad_detail", ad_id=ad_id)
    assert detail["title"] and detail["description"]
    seller = await call("get_ad_seller", ad_id=ad_id)
    assert seller["type"] in ("private", "dealer")


async def test_filtered_search_pages_on(call):
    result = await call("search_willhaben", keyword="iPhone 13", category=2722, require=["128gb"],
                        exclude=["pro", "max", "mini"], title_only=True, rows=3)
    assert len(result["results"]) == 3


async def test_cars_and_property(call):
    cars = await call("search_autos", make="BMW", model="3er", price_to=15000, rows=3)
    assert cars["results"]
    flats = await call("search_immobilien", property_type="Wohnung mieten", area="wien", rows=3)
    assert flats["results"]
    models = await call("list_car_models", make="BMW")
    assert models["count"] > 10


async def test_brand_lookup(call):
    brands = await call("search_brands", category=2722, term="")
    assert "count" in brands

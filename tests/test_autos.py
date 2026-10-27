"""Car search: off-filter listings are dropped, makes and models are named."""

import pytest

from samples import car, search

pytestmark = pytest.mark.anyio

AUTOS = "/restapi/v2/search/atz/3/2"


@pytest.fixture
def bmw(wh):
    wh.on("app-aggregator.willhaben.at", AUTOS, {"filterContainer": {"queryParameterName": "CAR_MODEL/MODEL",
          "options": [{"value": "1042", "label": "3er-Reihe"}, {"value": "1055", "label": "X3"}]}})
    return wh


async def test_a_paid_listing_outside_the_filters_is_dropped(bmw, call):
    bmw.on("ad-search.willhaben.at", AUTOS, search([
        car(1, "BMW 330d Touring", price=48880, year=2023, product_id=305),
        car(2, "BMW 320d", price=6999, year=2010),
        car(3, "BMW 318d", price=11490, year=2014),
    ], rows_found=1713))
    result = await call("search_autos", make="BMW", price_to=15000, year_from=2012)
    assert [r["id"] for r in result["results"]] == ["3"]
    assert result["dropped_off_filter"] == 2
    assert result["rows_found"] == 1713


async def test_hits_name_make_and_model(bmw, call):
    bmw.on("ad-search.willhaben.at", AUTOS, search([car(1, "BMW X3 xDrive20d", price=56900, model="1055")]))
    hit = (await call("search_autos", make="BMW"))["results"][0]
    assert (hit["make"], hit["model"], hit["year"]) == ("BMW", "X3", 2015)
    # the model list is fetched once per make, not per search
    await call("search_autos", make="BMW")
    assert len(bmw.sent(AUTOS)) == 3  # two searches, one model list (both share the path)

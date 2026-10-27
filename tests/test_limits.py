"""No single call can flood the context, whatever size it asks for."""

import pytest

import main
from conftest import ToolFailed
from samples import ad, search

pytestmark = pytest.mark.anyio


async def test_list_tools_clamp_their_limit(wh, call):
    categories = await call("list_categories", query="e", limit=5000)
    makes = await call("list_car_makes", limit=1000)
    assert len(categories["categories"]) == len(makes["makes"]) == main.MAX_LIST
    assert categories["count"] > main.MAX_LIST
    wh.on("app-aggregator.willhaben.at", "/api/v1/search/attribute-options", {"optionGroups": [
        {"options": [{"value": str(i), "label": f"Brand {i}"} for i in range(300)]}]})
    brands = await call("search_brands", category=2722, limit=1000)
    assert len(brands["brands"]) == main.MAX_LIST
    with pytest.raises(ToolFailed, match="limit must be at least 1"):
        await call("list_car_makes", limit=0)


async def test_searches_clamp_rows_and_reject_a_negative_offset(wh, call):
    wh.on("ad-search.willhaben.at", "/restapi/v2/search/", search([ad(i, f"Fahrrad {i}", price=100) for i in range(50)]))
    result = await call("search_willhaben", keyword="fahrrad", rows=500)
    assert len(result["results"]) == main.MAX_ROWS
    assert wh.sent("/restapi/v2/search/")[0].url.params["rows"] == str(main.MAX_ROWS)
    for tool in ("search_willhaben", "search_autos", "search_immobilien"):
        with pytest.raises(ToolFailed, match="offset must not be negative"):
            await call(tool, keyword="x", offset=-1)

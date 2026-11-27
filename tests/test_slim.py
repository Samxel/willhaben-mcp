"""Hits carry what an answer needs, not every field willhaben sends."""

import pytest

from samples import ad, detail, search

pytestmark = pytest.mark.anyio


async def test_search_hits_drop_duplicated_and_bulky_fields(wh, call):
    wh.on("ad-search.willhaben.at", "/restapi/v2/search/", search([
        ad(1, "iPhone 13 (reserviert)", price=300), ad(2, "Couch", price=None)]))
    first, second = (await call("search_willhaben", keyword="x"))["results"]
    for field in ("reserved", "image_url", "state", "paylivery"):
        assert field not in first, field
    assert (first["status"], first["price_amount"]) == ("reserved", 300.0) and "price" not in first
    assert first["url"] == "https://www.willhaben.at/iad/object?adId=1"
    assert first["published"] == "2026-10-27T23:07"
    # no amount: the display string is all there is
    assert "price_amount" not in second


async def test_detail_counts_images_and_links_the_ad(wh, call):
    wh.on("publicapi.willhaben.at", "/atdetail/v1/", detail(7, "iPhone 13", images=5))
    result = await call("get_ad_detail", ad_id="7")
    assert result["image_count"] == 5 and "image_urls" not in result
    assert result["url"] == "https://www.willhaben.at/iad/object?adId=7"
    assert result["condition"] == "Gebraucht" and "attributes" not in result


async def test_category_entries_carry_the_path_only(call):
    entry = (await call("list_categories", query="Smartphones / Handys"))["categories"][0]
    assert set(entry) <= {"id", "path", "has_children"}


async def test_a_path_splits_back_into_its_category_names(call):
    """Names carry slashes themselves ("Baby / Kind"), so levels join with ">"."""
    entry = (await call("list_categories", query="Kleider / Röcke"))["categories"][0]
    assert entry["path"].split(" > ")[0] == "Baby / Kind"
    assert entry["path"].endswith(" > Kleider / Röcke")

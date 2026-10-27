"""The tools end to end through an MCP client, against a fake willhaben."""

import httpx
import pytest

from samples import ad, car, detail, flat, search

pytestmark = pytest.mark.anyio

SEARCH = "/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz"
AUTOS = "/restapi/v2/search/atz/3/2"
IMMO = "/restapi/v2/search/atz/2/"
DETAIL = "/atdetail/v1/"


def paged(ads):
    """A search endpoint that serves `ads` by the request's offset and rows."""
    def respond(request):
        offset, rows = int(request.url.params["offset"]), int(request.url.params["rows"])
        return search(ads[offset:offset + rows], rows_found=len(ads))
    return respond


async def test_search_summarizes_hits(wh, call):
    wh.on("ad-search.willhaben.at", SEARCH, search([ad(1, "iPhone 13 128GB", price=350, body="Top Zustand")],
                                                    rows_found=4373))
    result = await call("search_willhaben", keyword="iPhone 13")
    hit = result["results"][0]
    assert (hit["id"], hit["price_amount"], hit["status"], hit["seller"]) == ("1", 350.0, "active", "privat")
    assert (result["rows_found"], result["has_more"]) == (4373, True)
    sent = wh.sent(SEARCH)[0].url.params
    assert (sent["keyword"], sent["rows"]) == ("iPhone 13", "4")


async def test_filters_page_on_until_enough_matches(wh, call):
    ads = ([ad(i, "iPhone 13 Pro 128GB", price=500) for i in range(60)]
           + [ad(100 + i, "iPhone 13", price=300, body="128 GB, Akku 90%") for i in range(3)])
    wh.on("ad-search.willhaben.at", SEARCH, paged(ads))
    result = await call("search_willhaben", keyword="iPhone 13", category=2722, require=["128gb"],
                        exclude=["pro"], title_only=True, rows=3)
    assert [r["id"] for r in result["results"]] == ["100", "101", "102"]
    assert result["scanned"] == 63
    assert wh.sent(SEARCH)[0].url.params["ATTRIBUTE_TREE"] == "2722"


async def test_handover_filter_reads_the_detail(wh, call):
    wh.on("ad-search.willhaben.at", SEARCH, search([ad(1, "iPhone 13", price=300), ad(2, "iPhone 13", price=310)]))
    wh.on("publicapi.willhaben.at", DETAIL + "1", detail(1, "iPhone 13", handover="Selbstabholung"))
    wh.on("publicapi.willhaben.at", DETAIL + "2", detail(2, "iPhone 13", handover="Selbstabholung, Versand"))
    result = await call("search_willhaben", keyword="iPhone 13", handover="versand", rows=1)
    assert [r["id"] for r in result["results"]] == ["2"]
    assert result["detail_lookups"] == 2
    assert wh.sent("/atdetail/v1/1")[0].headers["x-wh-application-token"] == "test-token"


async def test_detail_recovers_a_truncated_description(wh, call):
    short = "x" * 590
    wh.on("publicapi.willhaben.at", DETAIL, detail(7, "iPhone 13", description=short))
    page = ('<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"advertDetails":'
            '{"attributes":{"attribute":[{"name":"DESCRIPTION","values":["' + "y" * 900 + '<br/>Ende"]}]}}}}}'
            '</script></html>')
    wh.on("www.willhaben.at", "/iad/object", httpx.Response(200, text=page))
    result = await call("get_ad_detail", ad_id="7")
    assert result["description"].startswith("y" * 900) and result["description"].endswith("Ende")
    assert (result["ships"], result["pickup_only"]) == (True, False)


async def test_images_come_back_as_image_content(wh):
    from mcp import Client
    import main
    wh.on("publicapi.willhaben.at", DETAIL, detail(7, "iPhone 13", images=3))
    wh.on("cache.willhaben.at", "/mmo/", httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}))
    async with Client(main.mcp) as client:
        result = await client.call_tool("get_ad_images", {"ad_id": "7", "max_images": 2})
    assert [b.type for b in result.content] == ["text", "image", "image"]


async def test_seller_lookup_for_a_private_seller(wh, call):
    wh.on("publicapi.willhaben.at", DETAIL, detail(7, "iPhone 13"))
    wh.on("publicapi.willhaben.at", "/userprofile/trust-signals/", {"averageRating": 4.8, "numberOfRatings": 12,
                                                                     "replyTime": "innerhalb von 2 Std."})
    wh.on("ad-search.willhaben.at", "/restapi/v2/sellerprofile/", {"sellerProfile": {"registerDate": "2014-12-19"}})
    result = await call("get_ad_seller", ad_id="7")
    assert (result["type"], result["rating"], result["member_since"]) == ("private", 4.8, "2014-12-19")


async def test_car_search_maps_filters_and_power(wh, call):
    wh.on("ad-search.willhaben.at", AUTOS, search([car(1, "BMW 320d Touring", price=9800)]))
    result = await call("search_autos", make="BMW", fuel="Diesel", price_to=15000)
    hit = result["results"][0]
    assert (hit["power_kw"], hit["power_ps"], hit["mileage_km"]) == (140, 190, 150000)
    sent = wh.sent(AUTOS)[0].url.params
    assert (sent["CAR_MODEL/MAKE"], float(sent["PRICE_TO"])) == ("1005", 15000)


async def test_property_search_derives_price_per_square_metre(wh, call):
    wh.on("ad-search.willhaben.at", IMMO, search([flat(1, "2-Zimmer-Wohnung", price=1188)]))
    result = await call("search_immobilien", property_type="Wohnung mieten", rooms="2", area="wien")
    hit = result["results"][0]
    assert (hit["price_per_m2"], hit["living_area_m2"]) == (18.0, 66.0)
    assert wh.sent(IMMO)[0].url.params["NO_OF_ROOMS_BUCKET"] == "2X2"


async def test_category_and_make_lists(call):
    handys = await call("list_categories", query="handys")
    assert 2722 in [c["id"] for c in handys["categories"]]
    makes = await call("list_car_makes", query="bmw")
    assert makes["makes"][0]["label"] == "BMW"
    types = await call("list_immobilien_types")
    assert "Wohnung mieten" in [t["label"] for t in types["types"]]

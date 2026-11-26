"""District names and Vienna postcodes resolve to willhaben area ids."""

import pytest

from conftest import ToolFailed
from samples import ad, flat, search
from willhaben import regions

pytestmark = pytest.mark.anyio

SEARCH = "/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz"
IMMO = "/restapi/v2/search/atz/2/"


@pytest.mark.parametrize("value, label", [
    ("Neubau", "Wien, 07. Bezirk, Neubau"),
    ("1070", "Wien, 07. Bezirk, Neubau"),
    ("Graz", "Graz"),                       # not Graz-Umgebung
    ("innsbruck-land", "Innsbruck-Land"),
    ("Mürzzuschlag", "Bruck-Mürzzuschlag"),
])
def test_a_district_resolves_by_name_or_postcode(value, label):
    assert regions.resolve(value)["label"] == label


def test_an_unknown_district_says_what_works():
    with pytest.raises(regions.ToolError, match="Vienna postcode"):
        regions.resolve("Atlantis")


async def test_district_replaces_the_state_and_must_lie_in_it(wh, call):
    wh.on("ad-search.willhaben.at", SEARCH, search([]))
    await call("search_willhaben", keyword="rad", area="wien", district=["1070", "Leopoldstadt"])
    assert wh.sent(SEARCH)[0].url.params.get_list("areaId") == ["117229", "117224"]
    with pytest.raises(ToolFailed, match="Graz is not in wien"):
        await call("search_willhaben", keyword="rad", area="wien", district="Graz")


async def test_a_postcode_filters_hits_and_a_vienna_one_narrows_the_search(wh, call):
    ads = [flat(1, "Altbau", price=900, postcode="1070"), flat(2, "Neubau", price=950, postcode="1080")]
    wh.on("ad-search.willhaben.at", IMMO, search(ads))
    result = await call("search_immobilien", property_type="Wohnung mieten", postcode="1070")
    assert [r["id"] for r in result["results"]] == ["1"]
    assert wh.sent(IMMO)[0].url.params["areaId"] == "117229"

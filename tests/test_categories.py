"""Category names resolve when the words are right, and a near miss gets hints."""

import pytest

from willhaben import marktplatz
from conftest import ToolFailed
from samples import ad, search

pytestmark = pytest.mark.anyio


def test_a_name_in_other_order_resolves():
    assert marktplatz.resolve_category("Handys / Smartphones") == "2722"
    assert marktplatz.resolve_category("handys smartphones") == "2722"


async def test_a_near_miss_names_the_closest_categories(wh, call):
    with pytest.raises(ToolFailed, match=r"Closest: 3345 \(Mode / Accessoires / Damenmode / Schuhe\)"):
        await call("search_willhaben", keyword="sneaker", category="Damenschuhe")
    with pytest.raises(ToolFailed, match="Use list_categories"):
        await call("search_willhaben", keyword="x", category="Gibtsnicht")


async def test_search_sends_the_resolved_id(wh, call):
    wh.on("ad-search.willhaben.at", "/restapi/v2/search/", search([ad(1, "iPhone 13", price=300)]))
    await call("search_willhaben", keyword="iPhone 13", category="Handys / Smartphones")
    assert wh.sent("/restapi/v2/search/")[0].url.params["ATTRIBUTE_TREE"] == "2722"

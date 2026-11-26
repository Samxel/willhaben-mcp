"""What every client loads up front: the server instructions and tool listing."""

import inspect
import json

import pytest
from mcp import Client

from willhaben import server as main

pytestmark = pytest.mark.anyio


async def test_instructions_reach_the_client_and_name_every_tool():
    async with Client(main.mcp) as client:
        instructions = client.instructions
        tools = (await client.list_tools()).tools
    assert instructions == main.INSTRUCTIONS
    for tool in tools:
        assert tool.name in instructions, tool.name


async def test_descriptions_are_dedented_and_the_listing_stays_small():
    async with Client(main.mcp) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        assert tool.description == inspect.cleandoc(tool.description), tool.name
        assert not tool.description.startswith(" ")
    listing = json.dumps([tool.model_dump(exclude_none=True) for tool in tools], ensure_ascii=False)
    # ~19.3k before titles, null branches and doubled enums left the schemas
    # (~13.8k after); the car and property filters and district added ~1.3k
    assert len(listing) < 16000


async def test_schemas_carry_no_titles_null_branches_or_doubled_enums():
    async with Client(main.mcp) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        text = json.dumps(tool.input_schema)
        assert '"title"' not in text and '"null"' not in text, tool.name
    area = next(t for t in tools if t.name == "search_willhaben").input_schema["properties"]["area"]
    assert area["type"] == "array" and "enum" in area["items"]


async def test_a_single_value_still_passes_for_a_list_parameter(wh, call):
    from samples import search
    wh.on("ad-search.willhaben.at", "/restapi/v2/search/", search([]))
    await call("search_willhaben", keyword="rad", area="wien", condition="gebraucht")
    sent = wh.sent("/restapi/v2/search/")[0].url.params
    assert (sent["areaId"], sent.get_list("treeAttributes")) == ("900", ["23"])

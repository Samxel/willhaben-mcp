<h1 align="center">willhaben-mcp</h1>

<p align="center"><sub>From the same series: <a href="https://github.com/Samxel/geizhals-mcp">geizhals-mcp</a> · <a href="https://github.com/Samxel/kleinanzeigen-mcp">kleinanzeigen-mcp</a></sub></p>

An MCP server that lets an AI search [willhaben.at](https://www.willhaben.at)
and pull the full details of ads. It covers the marketplace (Marktplatz), the
Auto & Motor cars vertical and Immobilien (real estate), wrapping the willhaben
app's API and returning the important fields to the AI.
<p align="center">
  <a href="https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz"><img alt="Marktplatz listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fcoverage.json&query=%24.marktplatz&label=Marktplatz&color=green&suffix=%20listings&cacheSeconds=3600"></a>
  <a href="https://www.willhaben.at/iad/gebrauchtwagen/auto"><img alt="Auto & Motor listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fcoverage.json&query=%24.autos&label=Auto%20%26%20Motor&color=ff7300&suffix=%20listings&cacheSeconds=3600"></a>
  <a href="https://www.willhaben.at/iad/immobilien"><img alt="Immobilien listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fcoverage.json&query=%24.immobilien&label=Immobilien&color=1e6fff&suffix=%20listings&cacheSeconds=3600"></a>
</p>
<p align="center">
  <a href="https://github.com/Samxel/willhaben-mcp/graphs/traffic"><img alt="Clones" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fclones.json&query=%24.total_clones&label=Clones&color=6e7781&suffix=%20total&cacheSeconds=3600"></a>
</p>

<p align="center">
  <a href="#highlights">Highlights</a> ·
  <a href="#setup">Setup</a> ·
  <a href="#add-it-to-claude-and-other-mcp-clients">Add to Claude</a> ·
  <a href="#run-it-yourself">Run</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#disclaimer">Disclaimer</a> ·
  <a href="#license">License</a>
</p>

<p align="center"><sub><a href="README.de.md">Diese Anleitung auf Deutsch</a></sub></p>

## Highlights

- **Visual Listing Analysis**  
  Downloads and analyzes the actual ad photos, allowing vision-capable AI to inspect an item's condition, wear, damage, completeness, and other visual details instead of relying only on the seller's description. Small by default, sharp enough to read a label when asked.

- **Full access to willhaben filters**  
  Search with the complete range of available filters across Marketplace, Immobilien and Auto & Motor including categories, brands, condition, size, color, price, state and district (down to a Vienna postcode), vehicle specs, equipment, and much more.

- **From discovery to full inspection**  
  Find relevant ads, then retrieve complete listing details, full descriptions, attributes, precise locations, and all available photos for a deeper analysis, for one ad or up to ten at once.

## Setup

Pick one of three ways. All of them need nothing running in the background:
the client starts the server when it needs it.

**Claude Desktop, no terminal.** Download `willhaben-mcp.mcpb` from the
[latest release](https://github.com/Samxel/willhaben-mcp/releases/latest) and
double-click it. Claude Desktop shows an install dialog and sets up the
dependencies itself. Done; skip the rest of this section.

**With [uv](https://docs.astral.sh/uv/), no clone.** One command runs the
current version straight from GitHub:

```bash
uvx --from git+https://github.com/Samxel/willhaben-mcp willhaben-mcp --stdio
```

**From a clone.** Requires Python 3.10+.

```bash
git clone https://github.com/Samxel/willhaben-mcp
cd willhaben-mcp
pip install -r requirements.txt
python main.py --stdio
```

`uv run main.py --stdio` also works without the `pip install`: `main.py`
declares its own dependencies.

## Add it to Claude (and other MCP clients)

### Claude Code

One command, no config file:

```bash
claude mcp add willhaben -- uvx --from git+https://github.com/Samxel/willhaben-mcp willhaben-mcp --stdio
```

From a clone, point it at `main.py` instead:

```bash
claude mcp add willhaben -- python /path/to/willhaben-mcp/main.py --stdio
```

Add `--scope user` to have it in every project instead of only the current one.
`claude mcp list` shows whether the connection came up, `claude mcp remove
willhaben` takes it out again.

### Claude Desktop

The `.mcpb` from [Setup](#setup) is the easiest way. Without it, open
**Settings > Developer > Edit Config**, add the `willhaben` entry to
`claude_desktop_config.json` and restart Claude Desktop:

```json
{
  "mcpServers": {
    "willhaben": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Samxel/willhaben-mcp", "willhaben-mcp", "--stdio"]
    }
  }
}
```

From a clone, use `"command": "python"` and `"args":
["C:\\path\\to\\willhaben-mcp\\main.py", "--stdio"]` (the full path to
`main.py`, forward slashes on macOS/Linux).

Custom connectors (**Settings > Connectors**) connect from Anthropic's cloud,
so they cannot reach a server running only on your own machine. To use the
tools in claude.ai and the mobile apps, expose the HTTP server (see [Run it
yourself](#run-it-yourself)) through cloudflared, ngrok or a box with a public
hostname, and add that HTTPS URL under **Settings > Connectors > Add custom
connector**. Anyone who finds the URL can use the server too, though, so put
authentication in front of it.

Every tool is marked read-only, so clients that honour that hint won't ask
before each call. In Claude Desktop you can also open **Settings > Connectors >
willhaben** and set the tools' dropdown on the right to **Always allow**.

### Cursor, Codex, VS Code and other JSON-config clients

The same entry as for Claude Desktop, in the client's MCP config:

```json
{
  "mcpServers": {
    "willhaben": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Samxel/willhaben-mcp", "willhaben-mcp", "--stdio"]
    }
  }
}
```

Clients that connect to a URL instead take `"type": "http"` and `"url":
"http://127.0.0.1:8010/mcp"`, with the server started as below.

## Run it yourself

Without `--stdio`, the server starts over streamable HTTP and prints where it's
listening:

```bash
python main.py
```

```
Starting willhaben-mcp server on http://127.0.0.1:8010/mcp
```

Point your client at that URL, e.g. `claude mcp add --transport http willhaben
http://127.0.0.1:8010/mcp`. Host, port and path live at the top of
`willhaben/server.py`. Installed with pip or uv, the `willhaben-mcp` command
and `python -m willhaben` do the same as `python main.py`.

## Tools

The three searches share their region and paging parameters: `area` is an
Austrian state (`wien`, `niederoesterreich`, ...), and `district` narrows it to
districts (Bezirke) by name (`"Graz"`, `"Innsbruck-Land"`, `"Neubau"`) or
Vienna postcode (`"1070"`); `sort_by`, `rows` (up to 50), `offset`, and
`next_offset` / `has_more` to page on. Results come as compact JSON, and fields
without a value are left out. Search hits carry no link of their own: an ad's
page is `https://www.willhaben.at/iad/object?adId={id}`, which the server
instructions tell the AI once instead of repeating it in every hit.

`exclude`, `require` and `hide_reserved` work the same in all three searches,
applied here and paging on until `rows` matches are found or 250 ads have been
scanned:
- `exclude` drops titles carrying any of these words, e.g. `["pro", "max",
  "mini"]` to keep a base model clean of its own trim levels, or
  `["motorschaden", "export"]` for cars. It matches a whole word or a German
  compound tail (`"kabel"` catches `"Ladekabel"`) but never a mere prefix, so
  `"pro"` spares every `"Prozessor"`.
- `require` keeps hits where every term appears, with a number and its unit
  matched however it is spelled: `["128gb"]`, `["128 GB"]` and `["128 gb"]` are
  one filter and each finds all three spellings. Marketplace hits are checked
  in title and description; car and property hits carry no description, so
  there it is the title.
- `hide_reserved` drops reserved and sold ads.

<details>
<summary><h3>Marketplace</h3></summary>

**`search_willhaben(keyword, ...)`**

Search the marketplace. `keyword` is optional if you pass a `category`.

Filters:
- `category` (id or name; a name matches in any word order, and a miss
  suggests the closest categories)
- `condition` (new, used, refurbished, ...)
- `clothing_size` and `shoe_size`
- `color`, `pattern`
- `brand`
- region, seller type, price range, PayLivery (a payment method, not
  shipping), last 48h
- `title_only` (every keyword word must be in the **title**, not just anywhere
  in the ad text), plus `exclude`, `require` and `hide_reserved` from above
- **specs belong in `require`.** Putting them in `keyword` instead is the one
  mistake that quietly breaks a search. Sellers write the same storage size
  nine different ways and one ad in five leaves it out of the title entirely,
  so `keyword="iPhone 13 128 GB", title_only=True` finds almost nothing while
  `keyword="iPhone 13", require=["128gb"]` finds what you meant
- accessories are what none of these fix, because a case carries the phone's
  exact name too. Excluding nouns like `"case"` or `"akku"` backfires, since
  they appear in real listings' titles (`"Akku 100%"`). Use a `price_from` floor
  at ~15–20% of the product's real price instead (Geizhals'
  `get_model_price_range` gives you that number)
- `handover` (`"versand"` / `"abholung"`). Shipping is not in willhaben's
  search response at all, so this costs one detail request per surviving
  candidate, capped at 40 and reported as `detail_lookups`
- sorting and pagination

Returns a trimmed list of hits, each with a numeric `price_amount`. A
`status` ("reserved" / "sold") read out of the title appears only on ads that
are not active, because willhaben itself always reports an ad as active.
`rows` is 4 by default.

**`list_categories(query, parent_id, limit=50)`**

Find category ids for the `category` filter. Each entry carries its full path
("Baby / Kind > Kindermode > Mädchenmode > Kleider / Röcke") so similar names stay apart.
- `query`: search the whole tree by name
- `parent_id`: browse one level down (omit both for the top-level categories)

The full tree (~3500 categories) ships with the server in
`willhaben/data/marktplatz/categories.json`.

**`search_brands(category, term, limit=40)`**

Brands are category-specific and there are 1000+ per category, so this is a
type-ahead. Pass a category and a search term to get matching brand ids, then
hand an id to `search_willhaben(brand=...)`.

Note: condition, sizes, color, pattern and brand are category-dependent.
Applying them in a broad category can return zero hits, so drill into a
specific subcategory first.

</details>

<details>
<summary><h3>Auto & Motor</h3></summary>

**`search_autos(make, model, ...)`**

Search used cars (Gebrauchtwagen). All filters are optional.
- `make` (id or name) and `model` (id or name, e.g. "3er-Reihe")
- `car_type`, `fuel`, `transmission`, `wheel_drive`
- `condition` (Gebrauchtwagen, Neuwagen, Oldtimer, ...), `color`, `dealer`
- `equipment` (e.g. Sitzheizung, Anhängerkupplung)
- ranges: `price_from/to`, `year_from/to`, `mileage_from/to`, `power_from/to`
  (**kW**)
- `warranty`, `condition_report` (Pickerl §57a), region, last 48h, sorting, paging
- `exclude`, `require` and `hide_reserved` from above, e.g.
  `exclude=["motorschaden", "export", "bastler"]`

Enumerated filters accept the willhaben label or id, and an inverted range is
rejected instead of silently ignored. Results name the make and model and
include year, mileage, fuel and transmission. Power comes as both `power_kw`
and `power_ps`, since willhaben stores kW while ads and buyers talk in PS.

willhaben tops a make search with a paid dealer listing whatever the filters
say (a 48k car in an "up to 15k" search). Hits outside the requested ranges are
dropped and counted in `dropped_off_filter`. The
filter and make data ships in `willhaben/data/auto-motor/filters.json`.

**`list_car_makes(query, limit=60)`**

List car make ids for `search_autos`. Optional `query` filters by name.

**`list_car_models(make)`**

List the models of a make (fetched live, since models are make-specific).

</details>

<details>
<summary><h3>Immobilien</h3></summary>

**`search_immobilien(property_type, ...)`**

Search real estate. Pick a `property_type` first (buy vs rent and the kind of
property); everything else is optional.
- `property_type`: id or name, e.g. "Wohnung mieten", "Haus kaufen",
  "Grundstücke", "Gewerbeimmobilie mieten", or "Alle Immobilien" (default)
- `object_type` (e.g. "Einfamilienhaus", "Dachgeschosswohnung"), `rooms`
  ("1"-"5", "6-9", "10+")
- `features` (Garage, Keller, Einbauküche, ...), `outdoor` (Balkon, Terrasse,
  Garten, ...)
- ranges: `price_from/to`, `area_from/to` (living m²), `plot_from/to` (plot m²)
- `postcode`: exact postcodes such as `"1070"` or `"6020"`, checked on each
  hit. A Vienna postcode also narrows the search to its district at willhaben;
  pair one outside Vienna with `area` or `district`
- region and district, `exclude`, `require` and `hide_reserved` from above
  (e.g. `exclude=["befristet"]`), last 48h, sorting, paging

Filters that don't apply to the chosen type are ignored by willhaben; an
inverted range is rejected here rather than silently dropped. Results carry the
numeric `price_amount` and `price_per_m2` next to `living_area_m2`,
`plot_area_m2`, `rooms`, `floor`, `district` and `address`. The type and filter
data ships in `willhaben/data/immobilien/filters.json`.

**`list_immobilien_types()`**

List the real-estate property types for `search_immobilien`.

</details>

<details>
<summary><h3>Shared</h3></summary>

**`get_ad_detail(ad_id | ids)`**

Everything about one ad: the full description, itemised attributes, category
path, precise location and how many photos it has. Works across verticals. Run it on an id you
got from a search. The mobile API caps the description near 600 characters, so
when it looks cut off the full clean text is pulled from the ad's web page.
Pass up to 10 `ids` instead of `ad_id` to compare several in one call; they
come back as `{count, ads, failed}`, and an ad that is gone lands in `failed`
instead of failing the rest.

This is also the only place willhaben states delivery: `handover`
("Selbstabholung", "Versand") with `ships` / `pickup_only` alongside it.
PayLivery in the search is a payment method and says nothing about shipping.

**`get_ad_images(ad_id, max_images=4, detail="low")`**

Download an ad's photos server-side (at most 10) and return them as real image
content, so a vision-capable client sees the pictures instead of just URLs.
`detail="low"` (300×400, about 160 tokens per photo) is enough for condition
and completeness; `detail="high"` (900×1200, about 1400 tokens) reads small
print such as a label, a serial number or a screen.

**`get_ad_seller(ad_id)`**

Who is selling: name, private vs dealer, rating and reply time (private
sellers), the member-since / created date, and location. Useful for a trust or
plausibility check the ad itself does not answer.

</details>

## Disclaimer

This is an independent, unofficial project and is not affiliated with, endorsed
by, or connected to willhaben. "willhaben" and all related trademarks belong to
their respective owners.

It uses an unofficial interface of the willhaben app, which can change or go
away at any time. You are responsible for how you use it: respect willhaben's
Terms of Service and applicable law, don't hammer their servers, and don't
collect personal data from listings.

If you're a rights holder and have a concern about this repository, please open
an issue and I'll respond promptly.

## License

[`MIT LICENSE`](LICENSE). Provided "as is", without warranty.

<p align="center"><sub><a href="README.de.md">Diese Anleitung auf Deutsch</a></sub></p>

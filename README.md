<h1 align="center">willhaben-mcp</h1>

An MCP server that lets an AI search [willhaben.at](https://www.willhaben.at)
and pull the full details of ads. It covers the marketplace (Marktplatz), the
Auto & Motor cars vertical and Immobilien (real estate), wrapping willhaben's
reverse-engineered mobile-app API and returning the important fields to the AI.
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
  <a href="#run">Run</a> ·
  <a href="#add-it-to-claude-and-other-mcp-clients">Add to Claude</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#notes">Notes</a> ·
  <a href="#tests">Tests</a> ·
  <a href="#disclaimer">Disclaimer</a> ·
  <a href="#license">License</a>
</p>

## Highlights

- **Visual Listing Analysis**  
  Downloads and analyzes the actual ad photos, allowing vision-capable AI to inspect an item's condition, wear, damage, completeness, and other visual details instead of relying only on the seller's description.

- **Full access to willhaben filters**  
  Search with the complete range of available filters across Marketplace, Immobilien and Auto & Motor including categories, brands, condition, size, color, price, region, vehicle specs, equipment, and much more.

- **From discovery to full inspection**  
  Find relevant ads, then retrieve complete listing details, full descriptions, attributes, precise locations, and all available photos for a deeper analysis.

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

With [uv](https://docs.astral.sh/uv/) you can skip this step: `main.py`
declares its own dependencies, so `uv run main.py` installs them on the fly.

## Run

```bash
python main.py
```

The server starts over streamable HTTP and prints where it's listening:

```
Starting willhaben-mcp server on http://127.0.0.1:8010/mcp
```

Point your MCP client at that URL. Host, port and path live at the top of
`main.py`.

For clients that start the server themselves, `python main.py --stdio` speaks
MCP over stdin/stdout instead. Nothing has to run in the background then.

## Add it to Claude (and other MCP clients)

Clients that start their own servers use the stdio mode. Clients that connect
to a URL need `python main.py` running and take the URL it printed on startup.

### Claude Code

One command, no config file. Claude Code starts the server itself:

```bash
claude mcp add willhaben -- python /path/to/willhaben-mcp/main.py --stdio
```

Or connect to a server you already started with `python main.py`:

```bash
claude mcp add --transport http willhaben http://127.0.0.1:8010/mcp
```

Add `--scope user` to have it in every project instead of only the current one.
`claude mcp list` shows whether the connection came up, `claude mcp remove
willhaben` takes it out again.

### Cursor, Codex, VS Code and other JSON-config clients

Same URL, in the client's MCP config:

```json
{
  "mcpServers": {
    "willhaben": {
      "type": "http",
      "url": "http://127.0.0.1:8010/mcp"
    }
  }
}
```

### Claude Desktop

Custom connectors (**Settings > Connectors**) connect from Anthropic's cloud,
so they cannot reach a server running only on your own machine. Two ways
around it:

**Let Claude Desktop start it over stdio.** Open **Settings > Developer > Edit
Config**, add the `willhaben` entry to `claude_desktop_config.json` and restart
Claude Desktop:

```json
{
  "mcpServers": {
    "willhaben": {
      "command": "python",
      "args": ["C:\\path\\to\\willhaben-mcp\\main.py", "--stdio"]
    }
  }
}
```

Use the full path to `main.py` (forward slashes on macOS/Linux). With uv
installed, `"command": "uv"` and `"args": ["run", "/path/to/willhaben-mcp/main.py",
"--stdio"]` also takes care of the dependencies.

**Or expose the server** (cloudflared, ngrok, or run it on a box with a public
hostname) and add that HTTPS URL under **Settings > Connectors > Add custom
connector**. That way the tools also work in claude.ai and the mobile apps.
Anyone who finds the URL can use the server too, though, so put
authentication in front of it.

Every tool is marked read-only, so clients that honour that hint won't ask
before each call. In Claude Desktop you can also open **Settings > Connectors >
willhaben** and set the tools' dropdown on the right to **Always allow**.

## Tools

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
  in the ad text) and `exclude` (drop titles carrying any of these words, e.g.
  `["pro", "max", "mini"]` to keep a base model clean of its own trim levels).
  Both are applied here, paging on until `rows` matches are found or 250 ads have
  been scanned. `exclude` matches a whole word or a German compound tail
  (`"kabel"` catches `"Ladekabel"`) but never a mere prefix, so `"pro"` spares
  every `"Prozessor"`
- `require`: terms that must appear **anywhere in the title or description**,
  with a number and its unit matched however it is spelled: `["128gb"]`,
  `["128 GB"]` and `["128 gb"]` are one filter and each finds all three
  spellings. **This is where specs belong.** Putting them in `keyword` instead
  is the one mistake that quietly breaks a search. Sellers write the same
  storage size nine different ways and one ad in five leaves it out of the title
  entirely, so `keyword="iPhone 13 128 GB", title_only=True` finds almost
  nothing while `keyword="iPhone 13", require=["128gb"]` finds what you meant
- accessories are what none of these fix, because a case carries the phone's
  exact name too. Excluding nouns like `"case"` or `"akku"` backfires, since
  they appear in real listings' titles (`"Akku 100%"`). Use a `price_from` floor
  at ~15–20% of the product's real price instead (Geizhals'
  `get_model_price_range` gives you that number)
- `hide_reserved`
- `handover` (`"versand"` / `"abholung"`). Shipping is not in willhaben's
  search response at all, so this costs one detail request per surviving
  candidate, capped at 40 and reported as `detail_lookups`
- sorting and pagination

Returns a trimmed list of hits, each with a numeric `price_amount`, a short
`url` and a `status` ("active" / "reserved" / "sold") read out of the title,
because willhaben itself always reports an ad as active. `next_offset` and
`has_more` say where to continue; once everything is listed, `next_offset` is
left out. `rows` is capped at 50.

**`list_categories(query, parent_id, limit=50)`**

Find category ids for the `category` filter. Each entry carries its full path
("Baby / Kind > Kindermode > Mädchenmode > Kleider / Röcke") so similar names stay apart.
- `query`: search the whole tree by name
- `parent_id`: browse one level down (omit both for the top-level categories)

The full tree (~3500 categories) ships with the server in
`data/marktplatz/categories.json`.

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

Enumerated filters accept the willhaben label or id, and an inverted range is
rejected instead of silently ignored. Results name the make and model and
include year, mileage, fuel and transmission. Power comes as both `power_kw`
and `power_ps`, since willhaben stores kW while ads and buyers talk in PS.

willhaben tops a make search with a paid dealer listing whatever the filters
say (a 48k car in an "up to 15k" search). Hits outside the requested ranges are
dropped and counted in `dropped_off_filter`. The
filter and make data ships in `data/auto-motor/filters.json`.

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
- region, last 48h, sorting, paging

Filters that don't apply to the chosen type are ignored by willhaben; an
inverted range is rejected here rather than silently dropped. Results carry the
numeric `price_amount` and `price_per_m2` next to `living_area_m2`,
`plot_area_m2`, `rooms`, `floor`, `district` and `address`. The type and filter
data ships in `data/immobilien/filters.json`.

**`list_immobilien_types()`**

List the real-estate property types for `search_immobilien`.

</details>

<details>
<summary><h3>Shared</h3></summary>

**`get_ad_detail(ad_id)`**

Everything about one ad: the full description, itemised attributes, category
path, precise location and how many photos it has. Works across verticals. Run it on an id you
got from a search. The mobile API caps the description near 600 characters, so
when it looks cut off the full clean text is pulled from the ad's web page.

This is also the only place willhaben states delivery: `handover`
("Selbstabholung", "Versand") with `ships` / `pickup_only` alongside it.
PayLivery in the search is a payment method and says nothing about shipping.

**`get_ad_images(ad_id, max_images=4)`**

Download an ad's photos server-side (at most 10) and return them as real image
content, so a vision-capable client sees the pictures instead of just URLs.
Handy when you want to actually look at a listing.

**`get_ad_seller(ad_id)`**

Who is selling: name, private vs dealer, rating and reply time (private
sellers), the member-since / created date, and location. Useful for a trust or
plausibility check the ad itself does not answer.

</details>

## Notes

- The detail API only speaks HTTP/2 and needs an `x-wh-application-token`. That
  token is issued by willhaben (valid 30 days) in exchange for a signed request,
  so the server fetches a live token on demand and refreshes it automatically on
  a 401. There is no token to hand-edit. Only if willhaben rotates the signing
  key does the stored `WH_TOKEN_REQUEST` stop working; the comment above it in
  `willhaben/api.py` explains how to capture a fresh one from the app.
- Generated data lives under `data/`, one folder per vertical:
  `data/marktplatz/categories.json` (the ~3500-category tree),
  `data/auto-motor/filters.json` (car filters, options and makes) and
  `data/immobilien/filters.json` (property types, filters and options). The
  server loads them at startup. Re-crawl them if willhaben changes.
- `main.py` holds the MCP tools; the `willhaben/` package holds the API access
  (`api.py`), title matching, the marketplace filters, the car and property
  verticals, and the result summaries.
- Results go out as compact JSON with empty fields left out, and what all tools
  share is said once in the server instructions instead of in every tool
  description. Both keep what the model has to read small.
- The badges read `coverage.json` and `clones.json` from the `coverage` branch.
  A daily GitHub Action refreshes them there, so the counts don't add a commit
  to `main` every day.
- API details are documented in [`search_api.md`](search_api.md).
- This uses willhaben's internal API, not an official one. Be nice to it.

## Tests

```bash
pip install pytest
pytest
```

The suite runs offline against a fake willhaben and goes through a real MCP
client, stdio transport included. `WILLHABEN_LIVE=1 pytest` adds smoke tests
against the live API. CI runs the offline suite on every push and the live
tests once a day, which is what notices willhaben changing its app API or
rotating the token signing key.

## Disclaimer

This is an independent, unofficial project and is not affiliated with, endorsed
by, or connected to willhaben. "willhaben" and all related trademarks belong to
their respective owners.

It's published for educational and research purposes only. It talks to
willhaben's internal API, which is not meant for public use and may break or
change at any time. You are responsible for how you use it: respect willhaben's
Terms of Service, robots rules and applicable law, and don't hammer their
servers or process personal data from listings. No warranty of any kind; use at
your own risk.

If you're a rights holder and have a concern about this repository, please open
an issue and I'll respond promptly.

## License

[`MIT LICENSE`](LICENSE). Provided "as is", without warranty.

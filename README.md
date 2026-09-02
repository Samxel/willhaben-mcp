<h1 align="center">willhaben-mcp</h1>

An MCP server that lets an AI search [willhaben.at](https://www.willhaben.at)
and pull the full details of ads. It covers the marketplace (Marktplatz), the
Auto & Motor cars vertical and Immobilien (real estate), wrapping willhaben's
reverse-engineered mobile-app API and returning the important fields to the AI.

<p align="center">
  <a href="https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz"><img alt="Marktplatz listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fmain%2Fcoverage.json&query=%24.marktplatz&label=Marktplatz&color=green&suffix=%20listings&cacheSeconds=3600"></a>
  <a href="https://www.willhaben.at/iad/gebrauchtwagen/auto"><img alt="Auto & Motor listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fmain%2Fcoverage.json&query=%24.autos&label=Auto%20%26%20Motor&color=ff7300&suffix=%20listings&cacheSeconds=3600"></a>
  <a href="https://www.willhaben.at/iad/immobilien"><img alt="Immobilien listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fmain%2Fcoverage.json&query=%24.immobilien&label=Immobilien&color=1e6fff&suffix=%20listings&cacheSeconds=3600"></a>
</p>

## Highlights

- **Visual Listing Analysis**  
  Downloads and analyzes the actual ad photos, allowing vision-capable AI to inspect an item's **condition, wear, damage, completeness, and other visual details** instead of relying only on the seller's description.

- **Full access to willhaben filters**  
  Search with the **complete range of available filters** across Marketplace and Auto & Motor including categories, brands, condition, size, color, price, region, vehicle specs, equipment, and much more.

- **From discovery to full inspection**  
  Find relevant ads, then retrieve **complete listing details, full descriptions, attributes, precise locations, and all available photos** for a deeper analysis.

- **Search that stays on target**  
  willhaben matches a keyword against the whole ad text, so "RTX 4070" also returns brackets, cables and unrelated cards — worst of all sorted by price. `title_only` and `exclude` filter that out and page on until enough real matches are found, and every hit says whether it is already **reserved**.

## Tools

### Marketplace

**`search_willhaben(keyword, ...)`**

Search the marketplace. `keyword` is optional if you pass a `category`.

Filters:
- `category` (id or name)
- `condition` (new, used, refurbished, ...)
- `clothing_size` and `shoe_size`
- `color`, `pattern`
- `brand`
- region, seller type, price range, PayLivery, last 48h
- `title_only` (every keyword word must be in the **title**, not just anywhere
  in the ad text) and `exclude` (drop titles containing any of these words,
  e.g. `["verpackung", "ovp", "halterung"]`) — both applied here, paging on
  until `rows` matches are found or 250 ads have been scanned. Accessories and
  spare parts carry the product's exact name in their own title, so `title_only`
  won't remove them; a `price_from` floor at ~15–20% of the product's real price
  (from Geizhals' `get_model_price_range`) clears out nearly all of them without
  guessing any vocabulary
- `hide_reserved`
- `handover` (`"versand"` / `"abholung"`) — shipping is not in willhaben's
  search response at all, so this costs one detail request per surviving
  candidate, capped at 40 and reported as `detail_lookups`
- sorting and pagination

Returns a trimmed list of hits, each with a numeric `price_amount`, a `status`
("active" / "reserved" / "sold") and a `reserved` flag read out of the title —
willhaben itself always reports an ad as active. `next_offset` and `has_more`
say where to continue and when the catalogue is exhausted (`next_offset` is
`null` once it is).

**`list_categories(query, parent_id)`**

Find category ids for the `category` filter.
- `query`: search the whole tree by name
- `parent_id`: browse one level down (omit both for the top-level categories)

The full tree (~3500 categories) ships with the server in
`data/marktplatz/categories.json`.

**`search_brands(category, term)`**

Brands are category-specific and there are 1000+ per category, so this is a
type-ahead. Pass a category and a search term to get matching brand ids, then
hand an id to `search_willhaben(brand=...)`.

Note: condition, sizes, color, pattern and brand are category-dependent.
Applying them in a broad category can return zero hits, so drill into a
specific subcategory first.

### Auto & Motor

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
rejected instead of silently ignored. Results include the car fields (make,
model, year, mileage, fuel, transmission) and give power as both `power_kw` and
`power_ps`, since willhaben stores kW while ads and buyers talk in PS. The
filter and make data ships in `data/auto-motor/filters.json`.

**`list_car_makes(query)`**

List car make ids for `search_autos`. Optional `query` filters by name.

**`list_car_models(make)`**

List the models of a make (fetched live, since models are make-specific).

### Immobilien

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

### Shared

**`get_ad_detail(ad_id)`**

Everything about one ad: the full description, all images, itemised attributes,
category path and precise location. Works across verticals. Run it on an id you
got from a search. The mobile API caps the description near 600 characters, so
when it looks cut off the full clean text is pulled from the ad's web page.

This is also the only place willhaben states delivery: `handover`
("Selbstabholung", "Versand") with `ships` / `pickup_only` alongside it —
PayLivery in the search is a payment method and says nothing about shipping.

**`get_ad_images(ad_id, max_images=4)`**

Download an ad's photos server-side and return them as real image content
(base64), so a vision-capable client sees the pictures instead of just URLs.
Handy when you want to actually look at a listing.

**`get_ad_seller(ad_id)`**

Who is selling: name, private vs dealer, rating and reply time (private
sellers), the member-since / created date, and location. Useful for a trust or
plausibility check the ad itself does not answer.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

The server starts over streamable HTTP and prints where it's listening:

```
Starting willhaben-mcp server on http://127.0.0.1:8000/mcp
```

Point your MCP client at that URL. Host, port and path live at the top of
`main.py`.

## Add it to Claude (and other MCP clients)

The server speaks streamable HTTP, so most clients only need the URL it printed
on startup.

### Claude Code

One command, no config file:

```bash
claude mcp add --transport http willhaben http://127.0.0.1:8000/mcp
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
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### Claude Desktop

Custom connectors (**Settings > Connectors**) are dialled from Anthropic's
cloud, so they cannot reach a server bound to your own machine. Two ways
around it:

**Bridge over stdio** with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
(needs [Node.js](https://nodejs.org)). Open **Settings > Developer > Edit
Config**, add the `willhaben` entry to `claude_desktop_config.json` and restart
Claude Desktop:

```json
{
  "mcpServers": {
    "willhaben": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "mcp-remote", "http://127.0.0.1:8000/mcp"]
    }
  }
}
```

On macOS/Linux drop the Windows wrapper: `"command": "npx"` with
`"args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp"]`.

**Or expose the server** (cloudflared, ngrok, or run it on a box with a public
hostname) and add that HTTPS URL under **Settings > Connectors > Add custom
connector**. That route also makes the tools available in claude.ai and the
mobile apps, not just the desktop client but the API it talks to is then
reachable by whoever finds the URL, so put auth in front of it.

To stop confirming every call, open **Settings > Connectors > willhaben** and set
the tools' dropdown on the right to **Always allow**.

## Notes

- The detail API only speaks HTTP/2 and needs an `x-wh-application-token`. That
  token is issued by willhaben (valid 30 days) in exchange for a signed request,
  so `main.py` fetches a live token on demand and refreshes it automatically on a
  401 -- no token to hand-edit. Only if willhaben rotates the signing key does the
  stored `WH_TOKEN_REQUEST` stop working; the comment above it in `main.py`
  explains how to capture a fresh one from the app.
- Generated data lives under `data/`, one folder per vertical:
  `data/marktplatz/categories.json` (the ~3500-category tree),
  `data/auto-motor/filters.json` (car filters, options and makes) and
  `data/immobilien/filters.json` (property types, filters and options). `main.py`
  loads them at startup. Re-crawl them if willhaben changes.
- API details are documented in [`search_api.md`](search_api.md).
- This uses willhaben's internal API, not an official one. Be nice to it.

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

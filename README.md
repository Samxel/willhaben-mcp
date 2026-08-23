# willhaben-mcp

An MCP server that lets an AI search [willhaben.at](https://www.willhaben.at)
and pull the full details of ads. It covers the marketplace (Marktplatz) and
the Auto & Motor cars vertical, wrapping willhaben's reverse-engineered
mobile-app API and returning the important fields to the AI.

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
- sorting and pagination

Returns a trimmed list of hits.

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
- `make` (id or name) and `model` (id)
- `car_type`, `fuel`, `transmission`, `wheel_drive`
- `condition` (Gebrauchtwagen, Neuwagen, Oldtimer, ...), `color`, `dealer`
- `equipment` (e.g. Sitzheizung, Anhängerkupplung)
- ranges: `price_from/to`, `year_from/to`, `mileage_from/to`, `power_from/to`
- `warranty`, `condition_report` (Pickerl §57a), region, last 48h, sorting, paging

Enumerated filters accept the willhaben label or id. Results include car fields
(make, model, year, mileage, fuel, transmission, power). The filter and make
data ships in `data/auto-motor/filters.json`.

**`list_car_makes(query)`**

List car make ids for `search_autos`. Optional `query` filters by name.

**`list_car_models(make)`**

List the models of a make (fetched live, since models are make-specific).

### Shared

**`get_ad_detail(ad_id)`**

Everything about one ad: the full (untruncated) description, all images,
itemised attributes, category path and precise location. Works for marketplace
and car ads. Run it on an id you got from a search.

**`get_ad_images(ad_id, max_images=4)`**

Download an ad's photos server-side and return them as real image content
(base64), so a vision-capable client sees the pictures instead of just URLs.
Handy when you want to actually look at a listing.

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

## Notes

- The detail API is gated by a static app token and only speaks HTTP/2. Both are
  handled in `main.py`. If willhaben rotates the token, grab a fresh one from the
  app's request headers and replace `WH_APPLICATION_TOKEN`.
- Generated data lives under `data/`, one folder per vertical:
  `data/marktplatz/categories.json` (the ~3500-category tree) and
  `data/auto-motor/filters.json` (car filters, options and makes). `main.py`
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

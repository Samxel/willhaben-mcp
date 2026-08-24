# willhaben API

Two endpoints are used: the **Search API** (list of ads) and the **Detail API**
(one ad in full). Both are willhaben's internal mobile-app API.

# Search API

## Endpoint

```
GET https://ad-search.willhaben.at/restapi/v2/search/atz/seo/kaufen-und-verkaufen/marktplatz
```

## Headers

| Header | Value |
|---|---|
| `x-wh-client` | `api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app` |
| `x-wh-visitor-id` | random UUID, rotate per search |
| `Accept` | `application/json` |
| `Content-Type` | `application/json` |

## Query Params

| Param | Type | Description |
|---|---|---|
| `keyword` | string | search term (optional if `ATTRIBUTE_TREE` is given) |
| `rows` | int | results per page |
| `offset` | int | pagination offset |
| `sort` | int | see [Sort](#sort) |
| `sfId` | string (UUID) | session id, rotate per search |
| `isLog` | string | `"true"` |
| `areaId` | int or list[int] | see [Area](#area) |
| `ISPRIVATE` | int or list[int] | see [Seller](#seller) |
| `PRICE_FROM` | float | min price |
| `PRICE_TO` | float | max price |
| `paylivery` | string | `"true"` / `"false"` |
| `periode` | int | `2` = last 48h |
| `ATTRIBUTE_TREE` | int | category id, see [Categories](#categories) |
| `treeAttributes` | int or list[int] | condition, size, colour, pattern, brand; see [Tree attributes](#tree-attributes) |

### Sort

| Key | Value |
|---|---|
| actuality | 1 |
| nearest | 2 |
| price_asc | 3 |
| price_desc | 4 |
| relevance | 5 |

### Seller

| Key | Value |
|---|---|
| haendler | 0 |
| privat | 1 |

### Area

| Key | Value |
|---|---|
| burgenland | 1 |
| kaernten | 2 |
| niederoesterreich | 3 |
| oberoesterreich | 4 |
| salzburg | 5 |
| steiermark | 6 |
| tirol | 7 |
| vorarlberg | 8 |
| wien | 900 |
| andere_laender | 22000 |

List params (`areaId`, `ISPRIVATE`, `treeAttributes`) become repeated query keys,
e.g. `?areaId=5&areaId=6`.

### Categories

`ATTRIBUTE_TREE=<categoryId>` restricts the search to a category **and all its
subcategories**. It works with or without a `keyword`.

The category tree is navigated through the same search endpoint. A request with
`isNavigation=true&ATTRIBUTE_TREE=<id>` returns, under
`filterContainer.filterGroups[].filters[]`, a filter with
`filterType == "CategoryTreeFilter"` whose last `categoryGroups` entry lists the
**direct children** of `<id>`. Each child's own id is in `value`, `id` is
`category-<id>`, `behavior == "FETCH"` means it has children, and `all-in-*`
entries mean "everything in X" and can be ignored. Walking this recursively
yields the full tree; the crawled result ships as `category-tree.json`.

### Tree attributes

Condition, sizes, colour, pattern and **brand** are all carried by the single
multi-valued `treeAttributes` parameter. Combine them freely, e.g.
`?ATTRIBUTE_TREE=3439&treeAttributes=23&treeAttributes=3227` (used + size 44).
They are category-specific: an id that is invalid for the chosen category simply
yields zero hits.

**Condition**

| id | label |
|---|---|
| 22 | Neu |
| 2546 | Neuwertig |
| 5013256 | Generalüberholt |
| 23 | Gebraucht |
| 24 | Defekt |
| 2539 | Ausstellungsstück |

**Clothing size** uses two systems depending on the garment:
- numeric/combined: `5014962` (bis 34), `5014963` (36) up to `3227` (46 / S),
  `3228` (48 / M) up to `3233` (ab 58)
- letters: `4313` (XS), `4314` (S), `4315` (M), `4316` (L), `4317` (XL)

**Shoe size**: `4319` (bis 15), `5014897` to `5014916` (16 to 35), `4330` to
`4338` (36 to 44), `4339` to `4341` (45 to 47), `4342` (ab 48).

**Colour**: `3200` Mehrfarbig, `3201` Schwarz, `3202` Braun, `3203` Blau,
`3204` Türkis, `3205` Grün, `3206` Gelb, `3207` Orange, `3208` Rot, `3209` Rosa,
`3210` Violett, `3211` Grau, `3212` Silber, `3213` Gold, `3214` Beige,
`3215` Weiß.

**Pattern**: `5010052` Einfarbig, `5010064` Mustermix, `5010059` Farbverlauf,
`5010053` Floral, `5010058` Gepunktet, `5010054` Gestreift, `5010062`
Nadelstreifen, `5010057` Kariert, `5010063` Fischgrät, `5010061` Camouflage,
`5010060` Paisley, `5010055` Motivprint, `5010056` Animalprint, `5010065` Andere.

The full id maps live in `main.py`.

### Brands (attribute-options)

Brands are too many to enumerate (1000+ per category) and depend on the category,
so they are fetched on demand:

```
GET https://app-aggregator.willhaben.at/api/v1/search/attribute-options
    ?categoryId=<id>&attributeXmlCode=BRAND&navigatorId=Marke&term=<search>
```

Response: `optionGroups[].options[]` with `value` (the brand id) and `label`
(name; the matched substring is wrapped in `<b>` tags). Feed the chosen `value`
back as a `treeAttributes` value. A category without a brand filter answers `500`.

## Response

```
data["advertSummaryList"]["advertSummary"]  # list of ads
```

Each ad's `description` (attribute `BODY_DYN`) is **truncated** here. Use the
Detail API to get the full text.

# Auto & Motor API (cars)

The used-car vertical (Gebrauchtwagen) uses the same search endpoints as the
marketplace but a different vertical path, `atz/3/2`:

```
GET https://app-aggregator.willhaben.at/restapi/v2/search/atz/3/2   # navigation + filters
GET https://ad-search.willhaben.at/restapi/v2/search/atz/3/2        # result list
```

Headers are the same as the marketplace search. `isNavigation=true` returns the
`filterContainer` (available filters and their options); a plain request returns
`advertSummaryList` + `rowsFound`.

## Filter params

Enumerated filters (id lists ship in `data/auto-motor/filters.json`):

| Param | Filter | Notes |
|---|---|---|
| `CAR_MODEL/MAKE` | make | id (list in filters.json) |
| `CAR_MODEL/MODEL` | model | make-specific; fetch via a navigation request with `CAR_MODEL/MAKE=<id>` |
| `CAR_TYPE` | body type | 8 options |
| `ENGINE/FUEL` | fuel | Benzin 100001, Diesel 100003, Elektro 100004, ... |
| `TRANSMISSION` | gearbox | Automatik 180004, Schaltgetriebe 180001 |
| `WHEEL_DRIVE` | drive | Allrad 2, Hinterrad 1, Vorderrad 3 |
| `MOTOR_CONDITION` | condition | Gebrauchtwagen 20, Neuwagen 10, Oldtimer 93, ... |
| `EXTERIOR_COLOUR_MAIN` | colour | 15 options |
| `EQUIPMENT` | equipment | 115 options, multi-value |
| `DEALER` | seller | Händler 0, Privat 1 |

Range filters use `_FROM` / `_TO` pairs: `PRICE`, `YEAR_MODEL` (first
registration), `MILEAGE` (km), `ENGINEEFFECT` (power, kW), `BATTERY_CAPACITY`
(kWh), `WLTP_RANGE` (km), `NO_OF_DOORS`, `NO_OF_SEATS`.

Toggles: `WARRANTY=1`, `CONDITION_REPORT=1` (Pickerl §57a), `periode=2` (last 48h).
`areaId` and `sort` work as in the marketplace.

Multi-value params (selects, `EQUIPMENT`, `areaId`) become repeated query keys.
Query keys with a slash (`CAR_MODEL/MAKE`, `ENGINE/FUEL`) are sent URL-encoded.

## Result fields

Car ads carry extra attributes: `CAR_MODEL/MAKE`, `CAR_MODEL/MODEL`,
`CAR_MODEL/MODEL_SPECIFICATION`, `YEAR_MODEL`, `MILEAGE`, `ENGINE/FUEL_RESOLVED`,
`TRANSMISSION_RESOLVED`, `ENGINE/EFFECT` (PS), `CONDITION_RESOLVED`,
`NO_OF_OWNERS`.

# Immobilien API (real estate)

Real estate uses the vertical prefix `atz/2`, with the **property type as the
category id in the path**:

```
GET https://app-aggregator.willhaben.at/restapi/v2/search/atz/2/<propertyTypeId>   # navigation + filters
GET https://ad-search.willhaben.at/restapi/v2/search/atz/2/<propertyTypeId>        # result list
```

Each property type exposes a slightly different filter set. Types and options
ship in `data/immobilien/filters.json`.

## Property types

| id | type |
|---|---|
| 90 | Alle Immobilien |
| 131 | Wohnung mieten |
| 101 | Wohnung kaufen |
| 132 | Haus mieten |
| 102 | Haus kaufen |
| 14 | Grundstücke |
| 15 | Gewerbeimmobilie kaufen |
| 16 | Gewerbeimmobilie mieten |
| 12 | Ferienimmobilie kaufen |
| 32 | Ferienimmobilie mieten |
| 42 | Neubauprojekte |

## Filter params

| Param | Filter | Notes |
|---|---|---|
| `PROPERTY_TYPE` | object sub-type | 42 options (Einfamilienhaus, Villa, Dachgeschosswohnung, ...) |
| `NO_OF_ROOMS_BUCKET` | rooms | buckets: `1X1`..`5X5`, `6X9`, `10X` |
| `ESTATE_PREFERENCE` | fittings | Garage, Keller, Einbauküche, Fahrstuhl, ... |
| `FREE_AREA/FREE_AREA_TYPE` | outdoor | Balkon 20, Terrasse 10, Garten 60, Loggia 30, ... |
| `OWNAGETYPE` | ownership | Kauf 1, Miete 2, Pacht 3 |
| `areaId` | region | as in the marketplace |

Range filters use `_FROM` / `_TO` pairs: `PRICE`, `ESTATE_SIZE/LIVING_AREA`
(living m²), `ESTATE_SIZE/USEABLE_AREA` (usable m²), `PLOT/AREA` (plot m²),
`PRICE_SQM` (price per m²). Toggle `periode=2` = last 48h. Multi-value params and
slash keys (`ESTATE_SIZE/LIVING_AREA_FROM`, `FREE_AREA/FREE_AREA_TYPE`) are sent
URL-encoded. Filters that do not apply to the chosen type are ignored.

Car and property result ads carry type-specific attributes (e.g.
`ESTATE_SIZE/LIVING_AREA`, `PROPERTY_TYPE` for real estate).

# Detail API

Full data for a single ad by its id.

## Endpoint

```
GET https://publicapi.willhaben.at/atdetail/v1/{adId}
```

> **HTTP/2 is required.** Over HTTP/1.1 the endpoint answers `400`.

## Headers

| Header | Value / note |
|---|---|
| `Accept` | `application/json` |
| `x-wh-client` | `api@tailored-apps.com;willhabenapp;android;8.57.0;responsive_app` |
| `x-wh-security-version` | `20130527022532` |
| `x-wh-application-token` | signed app token (see below), **required**, else `401` |
| `x-wh-date` | any ISO-8601 timestamp; only its presence is checked, not the value |

`x-wh-visitor-id` and `user-agent` are **not** required.

### About the token

`x-wh-application-token` is **issued by willhaben** and validated (a wrong value
gives `401`). The app gets it from the `application-data` endpoint and reuses it
for ~30 days:

```
POST https://app-aggregator.willhaben.at/api/v1/application-data
{"applicationTokenRequest": {"organization": "...", "salt": "...",
                             "signature": "...", "timestamp": "..."}}
-> {"applicationToken": {"value": "...", "expireInSeconds": 2592000}}
```

`signature` is an HMAC over `organization + salt + timestamp` made with a key
baked into the app, so a fresh one can't be computed off-device. But the server
does **not** check the timestamp's age, so **replaying one captured signed request
keeps returning fresh 30-day tokens** -- that is how `main.py` auto-refreshes,
without hardcoding a token. Re-capture the signed `applicationTokenRequest` from
the app only if willhaben rotates the signing key (the stored one then `401`s).

`x-wh-date` and `x-wh-security-version` must be present on the detail request but
their values are not checked.

## Response

Widget-based layout (`data["widgets"]`, a list of typed blocks). The useful bits:

| `type` | Contains |
|---|---|
| `PICTURE_SLIDER` | `advertImageList[]` with `referenceImageUrl` (all images) |
| `TITLE_WITH_PRICE` | `formattedPrice`, `deliveryCosts`, `hasPaylivery` |
| `KEY_VALUE_PAIRS_LIST` | `keyValuePairsList[]`, itemised attributes (Marke, Zustand, and so on) |
| `PARAGRAPHED_TEXT` | `teaser` is the description, but **capped near 600 chars** (pick the longest; others are labels). See [Full description](#full-description) for the untruncated text. |
| `CATEGORIES` | `categoryPath.categoryEntryList[]`, the category path |

Flat, reliable fields live under
`data["taggingData"]["tmsDataValues"]["tmsData"]` (e.g. `seller_name`,
`seller_id`, `is_private`, `exact_price`, `post_code`,
`region_level_2`/`region_level_3`, `publish_date`), plus `data["adId"]`,
`data["description"]` (title) and `data["payliveryEnabled"]`.

## Full description

The detail API truncates the description to about 600 characters. The full,
clean text is on the ad's web page, embedded as JSON. Fetch
`https://www.willhaben.at/iad/object?adId=<id>` (follows a redirect, no token
needed), read the `<script id="__NEXT_DATA__">` JSON, and take
`props.pageProps.advertDetails.attributes.attribute[name=DESCRIPTION].values[0]`.

## Seller

The seller behind an ad comes from `tmsData`: `seller_id` and
`is_private` ("true"/"false"). With that:

| Who | Endpoint | Gives |
|---|---|---|
| private | `GET publicapi.willhaben.at/userprofile/trust-signals/<sellerId>` (no token) | `averageRating`, `numberOfRatings`, `replyTime` |
| private | `GET ad-search.willhaben.at/restapi/v2/sellerprofile/<sellerId>/5/profile` (token) | `sellerProfile.registerDate` (member since), name, `activeAdCount` |
| dealer | `GET api.willhaben.at/restapi/v2/dealerprofile/<orgId>` (token) | `organisation.orgName`, `created`, `addressDto`, `contactDto` |

The token-gated ones use the same `x-wh-application-token` as the detail API. A
seller's other ads are behind the `furtherAds` context link in the ad's
`SELLER_DETAILS_*` widget.

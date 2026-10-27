"""Raw ads and ad details cut down to the fields the model needs."""

from typing import Optional

from . import matching


def ad_attributes(ad: dict) -> dict:
    """Flatten willhaben's ``attributes.attribute`` list (a list of
    ``{"name": ..., "values": [...]}``) into a plain ``name -> value`` dict.
    Single-element value lists are unwrapped to their only value."""
    result: dict = {}
    for attr in ad.get("attributes", {}).get("attribute", []):
        values = attr.get("values", [])
        result[attr["name"]] = values[0] if len(values) == 1 else values
    return result


def ad_url(ad_id) -> Optional[str]:
    """The ad's short link; willhaben redirects it to the long SEO address, which
    is three times the length for the same page."""
    return f"https://www.willhaben.at/iad/object?adId={ad_id}" if ad_id else None


def to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def price_amount(attr: dict) -> Optional[float]:
    """The numeric price. Marketplace and car ads carry ``PRICE/AMOUNT``;
    property ads only have ``PRICE`` (or, for lettings, the monthly rent) --
    without the fallback, sorting and price-per-m2 are impossible on a value
    that is plainly there. An ad with none of these has no price at all
    ("auf Anfrage"), and neither does its display string."""
    for key in ("PRICE/AMOUNT", "PRICE", "RENT/PER_MONTH_LETTINGS"):
        amount = to_float(attr.get(key))
        if amount is not None:
            return amount
    return None


def summarize_ad(ad: dict) -> dict:
    """Reduce a full willhaben advert to the handful of fields that matter
    for an AI response instead of returning the whole raw object."""
    attr = ad_attributes(ad)
    title = attr.get("HEADING") or ad.get("description")
    amount = price_amount(attr)
    return {
        "id": ad.get("id"),
        "title": title,
        "description": attr.get("BODY_DYN"),
        "price_amount": amount,
        # the display string only says something the number does not when
        # there is no number ("auf Anfrage")
        "price": attr.get("PRICE_FOR_DISPLAY") if amount is None else None,
        "status": matching.ad_status(title, ad)[0],
        "seller": "privat" if attr.get("ISPRIVATE") == "1" else "haendler",
        "seller_name": attr.get("ORGNAME") or attr.get("CONTACT/NAME"),
        "location": attr.get("LOCATION"),
        "postcode": attr.get("POSTCODE"),
        "published": (attr.get("PUBLISHED_String") or "")[:16] or None,
        "paylivery": attr.get("p2penabled") == "true" or None,
        "url": ad_url(ad.get("id")),
    }


def widget(widgets: list[dict], widget_type: str) -> dict:
    """Return the first detail widget of the given type, or an empty dict."""
    return next((w for w in widgets if w.get("type") == widget_type), {})


def detail_attributes(detail: dict) -> dict:
    """The detail's itemised ``name -> value`` attributes ("Übergabe",
    "Zustand", ...), which live in the KEY_VALUE_PAIRS_LIST widget."""
    pairs = widget(detail.get("widgets", []), "KEY_VALUE_PAIRS_LIST").get("keyValuePairsList", [])
    return {kv.get("name"): kv.get("value") for kv in pairs}


def handover_options(attributes: dict) -> list[str]:
    """What the seller offers ("Selbstabholung", "Versand"). The only place
    willhaben states delivery -- it is not in the search response at all."""
    return [h.strip() for h in str(attributes.get("Übergabe") or "").split(",") if h.strip()]


def summarize_ad_detail(detail: dict) -> dict:
    """Reduce willhaben's widget-based advert-detail response to the fields that
    matter for an AI: the complete (untruncated) description, every image, the
    itemised attributes and precise location. Complements :func:`summarize_ad`,
    whose description is truncated."""
    widgets = detail.get("widgets", [])
    tms = detail.get("taggingData", {}).get("tmsDataValues", {}).get("tmsData", {})

    images = widget(widgets, "PICTURE_SLIDER").get("advertImageList", [])
    title_price = widget(widgets, "TITLE_WITH_PRICE")
    # Several PARAGRAPHED_TEXT widgets exist (e.g. the "Privatperson" label);
    # the real description is the one with the longest body.
    descriptions = [w.get("teaser", "") for w in widgets if w.get("type") == "PARAGRAPHED_TEXT"]
    description = max(descriptions, key=len) if descriptions else None

    attributes = detail_attributes(detail)
    title = detail.get("description")
    handover = handover_options(attributes)
    amount = to_float(tms.get("exact_price"))
    return {
        "id": detail.get("adId"),
        "title": title,
        "description": description or None,
        "price_amount": amount,
        "price": title_price.get("formattedPrice") if amount is None else None,
        "status": matching.ad_status(title, detail)[0],
        "condition": attributes.get("Zustand"),
        "handover": handover,
        "ships": any("versand" in h.lower() for h in handover),
        "pickup_only": bool(handover) and not any("versand" in h.lower() for h in handover),
        "seller": "privat" if tms.get("is_private") == "true" else "haendler",
        "seller_name": tms.get("seller_name"),
        "paylivery": detail.get("payliveryEnabled") or None,
        "location": tms.get("region_level_3"),
        "postcode": tms.get("post_code"),
        "state": tms.get("region_level_2"),
        "category": [kv.get("categoryName") for kv in
                     widget(widgets, "CATEGORIES").get("categoryPath", {}).get("categoryEntryList", [])],
        # condition and handover are pulled out above already
        "attributes": {k: v for k, v in attributes.items() if k not in ("Zustand", "Übergabe")} or None,
        "published": tms.get("publish_date"),
        "delivery": title_price.get("deliveryCosts"),
        # the pictures themselves come from get_ad_images; their urls would
        # only cost tokens here
        "image_count": len(images),
        "url": ad_url(detail.get("adId")),
    }

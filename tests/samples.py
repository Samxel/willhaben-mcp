"""Canned willhaben API responses, trimmed to the fields the server reads but
shaped like the live ones (see search_api.md), so the suite runs offline."""


def attrs(**values):
    """willhaben's ``attributes.attribute`` list from keyword arguments; a
    double underscore in a name stands for the slash willhaben uses."""
    return {"attribute": [{"name": k.replace("__", "/"), "values": v if isinstance(v, list) else [str(v)]}
                          for k, v in values.items() if v is not None]}


def ad(ad_id, title, *, price=None, body="", private=True, location="Wien, 02. Bezirk, Leopoldstadt",
       postcode="1020", state="Wien", seo=None, product_id=40020, **extra):
    return {
        "id": str(ad_id),
        "description": title,
        "productId": product_id,
        "advertStatus": {"id": "active"},
        "advertImageList": {"advertImage": [{"referenceImageUrl": f"https://cache.willhaben.at/mmo/{ad_id}.jpg"}]},
        "attributes": attrs(
            HEADING=title, BODY_DYN=body or None,
            PRICE__AMOUNT=price, PRICE_FOR_DISPLAY=f"€ {price:g}" if price is not None else None,
            ISPRIVATE="1" if private else "0", LOCATION=location, POSTCODE=postcode, STATE=state,
            PUBLISHED_String="2026-10-27T23:07:06Z",
            SEO_URL=seo or f"kaufen-und-verkaufen/d/{title.lower().replace(' ', '-')}-{ad_id}/",
            **extra),
    }


def car(ad_id, title, *, price, make="1005", model="1042", year=2015, mileage=150000, kw=140, **extra):
    return ad(ad_id, title, price=price, seo=f"gebrauchtwagen/d/auto/x-{ad_id}/", CAR_MODEL__MAKE=make,
              CAR_MODEL__MODEL=model, YEAR_MODEL=year, MILEAGE=mileage, ENGINE__EFFECT=kw,
              ENGINE__FUEL_RESOLVED="Diesel", TRANSMISSION_RESOLVED="Automatik",
              CONDITION_RESOLVED="Gebrauchtwagen", **extra)


def flat(ad_id, title, *, price, living=66, rooms=2, **extra):
    return ad(ad_id, title, price=price, seo=f"immobilien/d/mietwohnungen/x-{ad_id}/",
              ESTATE_SIZE__LIVING_AREA=living, NUMBER_OF_ROOMS=rooms, PROPERTY_TYPE="Wohnung",
              FLOOR=4, DISTRICT="Wien", ADDRESS="Leystraße", **extra)


def search(ads, *, rows_found=None):
    return {"rowsFound": len(ads) if rows_found is None else rows_found,
            "advertSummaryList": {"advertSummary": ads}}


def detail(ad_id, title, *, description="Funktioniert einwandfrei.", price=150, handover="Selbstabholung, Versand",
           private=True, seller_id="23628265", seller_name="AG", images=2):
    return {
        "adId": str(ad_id),
        "description": title,
        "payliveryEnabled": False,
        "taggingData": {"tmsDataValues": {"tmsData": {
            "exact_price": str(price), "is_private": "true" if private else "false",
            "seller_id": seller_id, "seller_name": seller_name, "region_level_3": "Salzburg Stadt",
            "post_code": "5020", "region_level_2": "Salzburg", "publish_date": "2026-10-20T10:00:00Z"}}},
        "widgets": [
            {"type": "PICTURE_SLIDER", "advertImageList": [
                {"referenceImageUrl": f"https://cache.willhaben.at/mmo/{ad_id}_{i}.jpg"} for i in range(images)]},
            {"type": "TITLE_WITH_PRICE", "formattedPrice": f"€ {price}", "deliveryCosts": None},
            {"type": "PARAGRAPHED_TEXT", "teaser": "Privatperson"},
            {"type": "PARAGRAPHED_TEXT", "teaser": description},
            {"type": "KEY_VALUE_PAIRS_LIST", "keyValuePairsList": [
                {"name": "Zustand", "value": "Gebraucht"}, {"name": "Übergabe", "value": handover}]},
            {"type": "CATEGORIES", "categoryPath": {"categoryEntryList": [
                {"categoryName": "Smartphones / Telefonie"}, {"categoryName": "Smartphones / Handys"}]}},
        ],
    }

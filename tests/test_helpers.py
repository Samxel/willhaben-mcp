"""Pure helpers: title matching, spec terms, ad status and lookups."""

import pytest

from willhaben import marktplatz, matching, summaries


def test_tokens_split_fused_model_codes():
    assert matching.tokens("Gigabyte RTX4070 Windforce") == {"gigabyte", "rtx", "4070", "windforce"}
    assert matching.tokens("Grün") == {"grun"}


def test_a_number_with_its_unit_matches_however_it_is_spelled():
    forms = matching.term_forms("128 GB")
    for title in ("iPhone 13 128GB", "iPhone 13 128 GB", "iPhone 13 128gb"):
        assert matching.has_term(matching.tokens(title), forms), title
    assert not matching.has_term(matching.tokens("iPhone 13 256GB"), forms)


def test_exclude_matches_words_and_compound_tails_but_not_prefixes():
    pro = matching.term_forms("pro")
    assert matching.has_term(matching.tokens("iPhone 13 Pro"), pro)
    assert not matching.has_term(matching.tokens("Intel Prozessor"), pro)
    assert matching.has_term(matching.tokens("USB Ladekabel"), matching.term_forms("kabel"))


@pytest.mark.parametrize("title, status", [
    ("iPhone 13 (reserviert)", "reserved"),
    ("*RESERVIERT* iPhone 13", "reserved"),
    ("iPhone 13 - verkauft", "sold"),
    ("Artikel werden nicht reserviert", "active"),
    ("iPhone 13 128GB", "active"),
])
def test_status_comes_from_the_title_marker(title, status):
    assert matching.ad_status(title)[0] == status


def test_price_amount_falls_back_for_property_ads():
    assert summaries.price_amount({"PRICE/AMOUNT": "150"}) == 150.0
    assert summaries.price_amount({"PRICE": "1192,72"}) == 1192.72
    assert summaries.price_amount({"RENT/PER_MONTH_LETTINGS": "900"}) == 900.0
    assert summaries.price_amount({}) is None


def test_categories_resolve_by_id_or_exact_name():
    assert marktplatz.resolve_category(2722) == "2722"
    assert marktplatz.resolve_category("Smartphones / Handys") == "2722"


def test_sizes_take_labels_or_ids():
    assert marktplatz.resolve_sizes(["38", "46 / S"], marktplatz.clothing_size_id, "clothing size") == ["5014964", "3227"]
    assert marktplatz.resolve_sizes("4332", marktplatz.shoe_size_id, "shoe size") == ["4332"]

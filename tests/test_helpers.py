"""Pure helpers: title matching, spec terms, ad status and lookups."""

import pytest

import main


def test_tokens_split_fused_model_codes():
    assert main._tokens("Gigabyte RTX4070 Windforce") == {"gigabyte", "rtx", "4070", "windforce"}
    assert main._tokens("Grün") == {"grun"}


def test_a_number_with_its_unit_matches_however_it_is_spelled():
    forms = main._term_forms("128 GB")
    for title in ("iPhone 13 128GB", "iPhone 13 128 GB", "iPhone 13 128gb"):
        assert main._has_term(main._tokens(title), forms), title
    assert not main._has_term(main._tokens("iPhone 13 256GB"), forms)


def test_exclude_matches_words_and_compound_tails_but_not_prefixes():
    pro = main._term_forms("pro")
    assert main._has_term(main._tokens("iPhone 13 Pro"), pro)
    assert not main._has_term(main._tokens("Intel Prozessor"), pro)
    assert main._has_term(main._tokens("USB Ladekabel"), main._term_forms("kabel"))


@pytest.mark.parametrize("title, status", [
    ("iPhone 13 (reserviert)", "reserved"),
    ("*RESERVIERT* iPhone 13", "reserved"),
    ("iPhone 13 - verkauft", "sold"),
    ("Artikel werden nicht reserviert", "active"),
    ("iPhone 13 128GB", "active"),
])
def test_status_comes_from_the_title_marker(title, status):
    assert main._ad_status(title)[0] == status


def test_price_amount_falls_back_for_property_ads():
    assert main._price_amount({"PRICE/AMOUNT": "150"}) == 150.0
    assert main._price_amount({"PRICE": "1192,72"}) == 1192.72
    assert main._price_amount({"RENT/PER_MONTH_LETTINGS": "900"}) == 900.0
    assert main._price_amount({}) is None


def test_categories_resolve_by_id_or_exact_name():
    assert main._resolve_category(2722) == "2722"
    assert main._resolve_category("Smartphones / Handys") == "2722"


def test_sizes_take_labels_or_ids():
    assert main._resolve_sizes(["38", "46 / S"], main.clothing_size_id, "clothing size") == ["5014964", "3227"]
    assert main._resolve_sizes("4332", main.shoe_size_id, "shoe size") == ["4332"]

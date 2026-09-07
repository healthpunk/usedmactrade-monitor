import os

import pytest

from parser import (Listing, ParserError, enrich_from_product_page, extract_specs, parse_category_page,
                    parse_chip, parse_price, parse_product_page, parse_ram_gb, parse_screen_inches, parse_ssd_gb)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("text,expected", [
    ("MacBook Pro 14 M3 Pro 18GB RAM", 18),
    ("MACBOOK PRO 14 M2 MAX / 32GB RAM / SSD 1TB", 32),
    ("MacBook Pro 14 M3 Pro 36GB", 36),
    ("MacBook Pro 14 M3 Max 48GB RAM 1TB", 48),
    ("MACBOOK PRO BLACK M4 MAX 16 core 64GB RAM VIDEO 40 CORE SSD 1000GB", 64),
    ("Оперативна пам'ять: 24GB Жорсткий диск 512GB", 24),
    ("ноутбук на 24 оперативки та 512GB SSD", 24),
    ("RAM 96 GB, SSD 2TB", 96),
    ("MacBook Pro 14 M3 Pro 512GB", None),   # no RAM stated -> None, not guessed
])
def test_ram_parsing(text, expected):
    assert extract_specs(text)["ram_gb"] == expected


def test_ram_direct_parser_ignores_ssd():
    assert parse_ram_gb("SSD 512GB") is None


@pytest.mark.parametrize("text,expected", [
    ("MacBook Pro 14 M1 Pro 16GB", "M1 Pro"),
    ("MacBook Pro 14 M2 Max 32GB", "M2 Max"),
    ("MacBook Pro 14 M3 8GB", "M3"),
    ("MACBOOK PRO 14 M3 PRO 18GB", "M3 Pro"),
    ("MacBook Pro 14 M3 MAX 36GB", "M3 Max"),
    ("MACBOOK PRO 14 M4 PRO 12CORE VIDEO 16 CORE", "M4 Pro"),
    ("MACBOOK PRO 14 M5 BLACK / 24GB RAM", "M5"),
    ("MacBook Pro 14 M4-Pro", "M4 Pro"),
    ("MacBook Air 15 Intel i7", None),
])
def test_chip_parsing(text, expected):
    assert parse_chip(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("SSD 512B", 512),          # site typo
    ("SSD 1000GB", 1000),
    ("1TB SSD", 1000),
    ("SSD-2TB", 2000),
    ("Жорсткий диск 512GB", 512),
    ("48GB RAM 1TB", 1000),      # unlabeled fallback via extract_specs
])
def test_ssd_parsing(text, expected):
    assert (parse_ssd_gb(text) or extract_specs(text)["ssd_gb"]) == expected


@pytest.mark.parametrize("text,expected", [
    ("MACBOOK PRO 14 M4 PRO 14 CORE VIDEO 20 CORE", 14),   # '14 CORE' must not win
    ("КАСТОМНА 14-ка 48GB RAM з 16-дюймової моделі", 14),
    ("Macbook Air 15 M4", 15),
    ('MacBook Pro 16" M1 Max', 16),
    ("MACBOOK PRO BLACK M4 MAX 16 core", None),
])
def test_screen_parsing(text, expected):
    assert parse_screen_inches(text) == expected


@pytest.mark.parametrize("text,price,cur", [
    ("$1 790.00", 1790.0, "USD"),
    ("$1,599", 1599.0, "USD"),
    ("1 990,00 €", 1990.0, "EUR"),
    ("65 000 грн", 65000.0, "UAH"),
    ("", None, None),
])
def test_price_parsing(text, price, cur):
    assert parse_price(text) == (price, cur)


def test_full_title_extraction():
    s = extract_specs("MACBOOK PRO 14 M4 PRO 12CORE VIDEO 16 CORE / BLACK / 24GB RAM / SSD 512B / "
                      "лише 30 циклів / офіційна гаратнія APPLE CARE 07/2027!")
    assert s["model"] == "MacBook Pro"
    assert s["screen_inches"] == 14
    assert s["chip"] == "M4 Pro"
    assert s["ram_gb"] == 24
    assert s["ssd_gb"] == 512
    assert s["battery_cycles"] == 30
    assert s["warranty"] == "AppleCare until 07/2027"
    assert s["battery_health_pct"] is None


def test_health_and_condition():
    s = extract_specs("ІДЕАЛ MACBOOK PRO 14 M3 MAX 36GB RAM SSD 1TB 1 цикл 100% Apple Care 01/2027")
    assert s["battery_health_pct"] == 100
    assert s["battery_cycles"] == 1
    assert s["condition"] == "ideal"
    assert s["chip"] == "M3 Max"


def test_category_fixture_parses_both_listings():
    listings = parse_category_page(fixture("category_pro14_2026-09-07.html"), default_screen_inches=14)
    assert [l.id for l in listings] == ["861950816", "862217066"]
    a, b = listings
    assert a.url.endswith("-p861950816")
    assert (a.price, a.currency) == (1790.0, "USD")
    assert (a.model, a.screen_inches, a.chip, a.ram_gb, a.ssd_gb, a.battery_cycles) == \
        ("MacBook Pro", 14, "M4 Pro", 24, 512, 30)
    assert a.image_url.startswith("https://d2j6dbq0eux0bg.cloudfront.net/")
    assert (b.price, b.chip, b.ram_gb, b.ssd_gb, b.battery_cycles) == (1990.0, "M5", 24, 1000, 7)
    assert b.warranty == "AppleCare until 04/2027"


def test_product_page_fixture_enriches():
    data = parse_product_page(fixture("product_861950816.html"))
    assert data["title"].startswith("MACBOOK PRO 14 M4 PRO")
    assert data["price"] == 1790.0
    assert "Оперативна пам" in data["description"]
    listing = Listing(id="861950816", title="x", url="u")
    enrich_from_product_page(listing, fixture("product_861950816.html"))
    assert listing.ram_gb == 24 and listing.ssd_gb == 512 and listing.battery_cycles == 30


def test_missing_grid_raises():
    with pytest.raises(ParserError):
        parse_category_page("<html><body><h1>Oops</h1></body></html>")


def test_empty_grid_is_legit_empty():
    html = '<div class="grid__products" data-items="0"></div>'
    assert parse_category_page(html) == []


def test_unreadable_cards_raise():
    html = '<div class="grid__products" data-items="1"><div class="grid-product">broken</div></div>'
    with pytest.raises(ParserError):
        parse_category_page(html)

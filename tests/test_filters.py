import pytest

from filters import Criteria, evaluate
from parser import Listing, apply_specs

BASE = Criteria(model="MacBook Pro", screen_inches=14, min_ram_gb=32, preferred_chips=("M3", "M3 Pro", "M3 Max"))


def listing(title, **kw):
    l = Listing(id="1", title=title, url="https://x/p1", **kw)
    return apply_specs(l, title)


@pytest.mark.parametrize("title", [
    "MacBook Pro 14 M3 Pro 36GB RAM 512GB SSD",
    "MacBook Pro 14 M3 Max 48GB RAM 1TB SSD",
    "MacBook Pro 14 M4 Pro 48GB RAM 1TB SSD",
    "MacBook Pro 14 M2 Max 32GB RAM 1TB SSD",
    "MACBOOK PRO 14 M1 MAX 64GB RAM SSD 2TB",
])
def test_matches(title):
    assert evaluate(listing(title), BASE).matched, title


@pytest.mark.parametrize("title", [
    "MacBook Pro 14 M3 Pro 18GB RAM 512GB",
    "MacBook Pro 16 M3 Max 36GB RAM 1TB",
    "MacBook Air 15 M3 32GB RAM 512GB",  # wrong model, screen too
    "MacBook Pro 14 M4 24GB RAM 512GB",
])
def test_non_matches(title):
    res = evaluate(listing(title), BASE)
    assert not res.matched and res.reasons, title


def test_preferred_flag_never_excludes():
    m3 = evaluate(listing("MacBook Pro 14 M3 Pro 36GB RAM 1TB"), BASE)
    m4 = evaluate(listing("MacBook Pro 14 M4 Pro 36GB RAM 1TB"), BASE)
    assert m3.matched and m3.preferred
    assert m4.matched and not m4.preferred


def test_unknown_ram_is_conservative():
    l = listing("MacBook Pro 14 M3 Pro 1TB SSD")   # no RAM stated
    assert l.ram_gb is None
    res = evaluate(l, BASE)
    assert not res.matched and "ram_gb" in res.unknown
    lenient = Criteria(**{**BASE.__dict__, "match_unknown_fields": True})
    assert evaluate(l, lenient).matched


def test_future_constraints():
    c = Criteria(**{**BASE.__dict__, "max_price_usd": 1800, "max_battery_cycles": 150,
                    "min_ssd_gb": 1000, "allowed_chips": ("M3 Pro", "M3 Max", "M4 Pro")})
    good = listing("MacBook Pro 14 M3 Pro 36GB RAM 1TB SSD 42 цикли", price=1599.0, currency="USD")
    assert evaluate(good, c).matched
    pricey = listing("MacBook Pro 14 M3 Pro 36GB RAM 1TB SSD 42 цикли", price=2500.0, currency="USD")
    assert "price" in " ".join(evaluate(pricey, c).reasons)
    tired = listing("MacBook Pro 14 M3 Pro 36GB RAM 1TB SSD 400 циклів", price=1599.0, currency="USD")
    assert "battery_cycles" in " ".join(evaluate(tired, c).reasons)
    small = listing("MacBook Pro 14 M3 Pro 36GB RAM 512GB SSD 42 цикли", price=1599.0, currency="USD")
    assert "ssd_gb" in " ".join(evaluate(small, c).reasons)
    wrong_chip = listing("MacBook Pro 14 M2 Max 64GB RAM 1TB SSD 42 цикли", price=1599.0, currency="USD")
    assert "chip" in " ".join(evaluate(wrong_chip, c).reasons)

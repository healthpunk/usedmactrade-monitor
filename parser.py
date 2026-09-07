"""Parse UsedMacTrade (an Ecwid storefront) category and product pages.

The category grid is server-rendered, so a plain HTTP GET is enough. Every
listing is normalized into a ``Listing`` whose spec fields are extracted from
free-text titles/descriptions with tolerant, context-anchored regexes. A field
that cannot be parsed stays ``None`` (never guessed).
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from urllib.parse import unquote

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


class ParserError(RuntimeError):
    """The page loaded but did not look like the storefront we know."""


@dataclass
class Listing:
    id: str
    title: str
    url: str
    price: float | None = None
    currency: str | None = None
    model: str | None = None
    screen_inches: int | None = None
    chip: str | None = None
    ram_gb: int | None = None
    ssd_gb: int | None = None
    battery_cycles: int | None = None
    battery_health_pct: int | None = None
    condition: str | None = None
    warranty: str | None = None
    image_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        known = {k: d.get(k) for k in cls.__dataclass_fields__}
        return cls(**known)


# --------------------------------------------------------------------------- #
# Spec extraction (title / description text)
# --------------------------------------------------------------------------- #
_SIZE_UNIT = r"(?:GB|ГБ|Gb|G|TB|ТБ|Tb|T)"
_RAM_ANCHOR = r"(?:RAM|ОЗУ|ОЗП|оператив\w*(?:\s+пам\S*)?|памʼят\w*|пам'ят\w*|пам’ят\w*|memory)"
_SSD_ANCHOR = r"(?:SSD|ССД|NVMe|диск\w*|накопичувач\w*|storage|сховищ\w*)"

RE_MODEL = re.compile(r"mac\s*book\s*(pro|air)", re.I)
RE_OTHER_MAC = re.compile(r"\b(imac|mac\s*mini|mac\s*studio|mac\s*pro)\b", re.I)
RE_CHIP = re.compile(r"\bM(\d)\s*-?\s*(PRO|MAX|ULTRA)?\b", re.I)
RE_RAM = [
    # "24GB RAM", "36 ГБ оперативної пам'яті", "32GB RAM"
    re.compile(rf"(\d{{1,3}})\s*{_SIZE_UNIT}?\s*{_RAM_ANCHOR}", re.I),
    # "RAM 36GB", "Оперативна пам'ять: 24GB", "RAM: 48"
    re.compile(rf"{_RAM_ANCHOR}\s*[:\-–]?\s*(\d{{1,3}})\s*{_SIZE_UNIT}?\b", re.I),
    # "на 24 оперативки"
    re.compile(r"на\s*(\d{1,3})\s*(?:GB|ГБ)?\s*оперативк", re.I),
]
RE_SSD = [
    # "SSD 512B" (site typo), "SSD 1000GB", "SSD-1TB", "Жорсткий диск 512GB"
    re.compile(rf"{_SSD_ANCHOR}\s*[:\-–]?\s*(\d{{1,4}})\s*(TB|ТБ|Tb|T|GB|ГБ|Gb|G|B)?\b", re.I),
    # "1TB SSD", "512GB SSD"
    re.compile(rf"(\d{{1,4}})\s*(TB|ТБ|Tb|T|GB|ГБ|Gb|G)\s*{_SSD_ANCHOR}", re.I),
]
RE_ANY_SIZE = re.compile(r"(\d{1,4})\s*(TB|ТБ|GB|ГБ)\b", re.I)
RE_SCREEN = [
    # "MACBOOK PRO 14 M4", "Air 15 M4", "Retina 14" - but not "PRO 14 CORE"
    re.compile(r"(?:PRO|AIR|RETINA|MACBOOK)\s*-?\s*(13|14|15|16)\b(?!\s*(?:-?\s*(?:GB|ГБ|TB|ТБ|core|ядер|цикл|%)))", re.I),
    # '14-ка', '14"', '14 дюйм', '14 inch', '14-inch'
    re.compile(r"\b(13|14|15|16)\s*(?:-?\s*ка\b|[\"”″]|-?\s*дюйм|-?\s*inch|')", re.I),
]
RE_CYCLES = [
    re.compile(r"(\d{1,4})\s*(?:цикл\w*|cycles?\b)", re.I),
    re.compile(r"(?:цикл\w*|cycles?)\s*[:\-–]?\s*(\d{1,4})\b", re.I),
]
RE_HEALTH = re.compile(r"(\d{2,3})\s*%")
RE_WARRANTY = re.compile(
    r"(apple\s*care\+?|гарант\w*|warranty)[^\d\n]{0,25}?(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}[./]\d{4}|\d{4})",
    re.I,
)
CONDITION_KEYWORDS = [
    (re.compile(r"новий\s+у\s+плівк|нова\s+у\s+плівк|sealed", re.I), "new sealed"),
    (re.compile(r"як\s+нов", re.I), "like new"),
    (re.compile(r"ідеал", re.I), "ideal"),
    (re.compile(r"б/у|вживан", re.I), "used"),
]


def _to_gb(value: int, unit: str | None) -> int:
    if unit and unit.upper().startswith(("T", "Т")):
        return value * 1000   # site convention: 1TB == 1000GB
    return value


def _first_int(patterns, text: str, lo: int, hi: int) -> int | None:
    for pat in patterns:
        for m in pat.finditer(text):
            try:
                v = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if lo <= v <= hi:
                return v
    return None


def parse_chip(text: str) -> str | None:
    m = RE_CHIP.search(text)
    if not m:
        return None
    chip = f"M{m.group(1)}"
    if m.group(2):
        chip += " " + m.group(2).capitalize()
    return chip


def parse_ram_gb(text: str) -> int | None:
    return _first_int(RE_RAM, text, 4, 512)


def parse_ssd_gb(text: str) -> int | None:
    for pat in RE_SSD:
        for m in pat.finditer(text):
            value, unit = int(m.group(1)), m.group(2)
            if unit and unit.upper() == "B":      # "SSD 512B" typo -> GB
                unit = "GB"
            gb = _to_gb(value, unit)
            if 64 <= gb <= 16000:
                return gb
    return None


def parse_screen_inches(text: str) -> int | None:
    return _first_int(RE_SCREEN, text, 13, 16)


def parse_model(text: str) -> str | None:
    m = RE_MODEL.search(text)
    if m:
        return f"MacBook {m.group(1).capitalize()}"
    m = RE_OTHER_MAC.search(text)
    if m:
        return " ".join(m.group(1).split()).title().replace("Imac", "iMac")
    return None


def parse_battery_cycles(text: str) -> int | None:
    return _first_int(RE_CYCLES, text, 0, 5000)


def parse_battery_health_pct(text: str) -> int | None:
    for m in RE_HEALTH.finditer(text):
        v = int(m.group(1))
        if 50 <= v <= 100:
            return v
    return None


def parse_warranty(text: str) -> str | None:
    m = RE_WARRANTY.search(text)
    if not m:
        return None
    kind = "AppleCare" if "care" in m.group(1).lower() else "Warranty"
    return f"{kind} until {m.group(2)}"


def parse_condition(text: str) -> str | None:
    for pat, label in CONDITION_KEYWORDS:
        if pat.search(text):
            return label
    return None


def parse_price(text: str | None) -> tuple[float | None, str | None]:
    """'$1 790.00' -> (1790.0, 'USD'); tolerant of thin spaces and EU formats."""
    if not text:
        return None, None
    t = text.replace("\xa0", " ").replace(" ", " ").strip()
    currency = None
    for sym, code in (("$", "USD"), ("€", "EUR"), ("₴", "UAH"), ("грн", "UAH"), ("zł", "PLN"),
                      ("USD", "USD"), ("EUR", "EUR"), ("UAH", "UAH"), ("PLN", "PLN")):
        if sym in t:
            currency = code
            break
    m = re.search(r"\d[\d\s.,]*", t)
    if not m:
        return None, currency
    num = m.group(0).replace(" ", "")
    if "," in num and "." in num:
        dec = "," if num.rfind(",") > num.rfind(".") else "."
        num = num.replace("." if dec == "," else ",", "").replace(",", ".")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = f"{head}.{tail}" if len(tail) == 2 else num.replace(",", "")
    try:
        return float(num), currency
    except ValueError:
        return None, currency


def extract_specs(text: str) -> dict:
    """Run every spec parser over ``text``. Missing values are None."""
    text = " ".join(text.split())
    specs = {
        "model": parse_model(text),
        "screen_inches": parse_screen_inches(text),
        "chip": parse_chip(text),
        "ram_gb": parse_ram_gb(text),
        "ssd_gb": parse_ssd_gb(text),
        "battery_cycles": parse_battery_cycles(text),
        "battery_health_pct": parse_battery_health_pct(text),
        "condition": parse_condition(text),
        "warranty": parse_warranty(text),
    }
    # Fallback for unlabeled sizes ("... 36GB / 1TB ..."): smaller = RAM, larger = SSD.
    if specs["ram_gb"] is None or specs["ssd_gb"] is None:
        sizes = sorted({_to_gb(int(v), u) for v, u in RE_ANY_SIZE.findall(text)})
        sizes = [s for s in sizes if s not in (specs["ram_gb"], specs["ssd_gb"])]
        if specs["ram_gb"] is None and specs["ssd_gb"] is None and len(sizes) == 2:
            specs["ram_gb"], specs["ssd_gb"] = sizes
        elif specs["ram_gb"] is None and len(sizes) == 1 and sizes[0] <= 256:
            specs["ram_gb"] = sizes[0]
        elif specs["ssd_gb"] is None and len(sizes) == 1 and sizes[0] >= 128:
            specs["ssd_gb"] = sizes[0]
    return specs


def apply_specs(listing: Listing, text: str, *, overwrite: bool = False) -> Listing:
    """Fill listing fields from ``text``; by default only fills None fields."""
    for k, v in extract_specs(text).items():
        if v is not None and (overwrite or getattr(listing, k) is None):
            setattr(listing, k, v)
    return listing


# --------------------------------------------------------------------------- #
# Page parsing
# --------------------------------------------------------------------------- #
def parse_category_page(html: str, *, default_screen_inches: int | None = None) -> list[Listing]:
    """Return every listing on a category page (one ``?offset=`` page).

    Raises ``ParserError`` when the grid container is missing or when cards are
    present but unreadable, so a layout change never masquerades as "no stock".
    """
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one(".grid__products")
    if grid is None:
        raise ParserError("no '.grid__products' container found; page layout may have changed")
    cards = grid.select(".grid-product")
    declared = grid.get("data-items")
    declared_n = int(declared) if declared and declared.isdigit() else None

    listings: list[Listing] = []
    for card in cards:
        wrap = card.select_one("[data-product-id]")
        title_el = card.select_one(".grid-product__title-inner")
        link = card.select_one("a.grid-product__title") or card.select_one("a.grid-product__image")
        if wrap is None or link is None:
            log.warning("skipping product card without id/link: %s", str(card)[:200])
            continue
        pid = wrap["data-product-id"].strip()
        title = " ".join((title_el.get_text(" ") if title_el else link.get("title", "")).split())
        url = link["href"]
        price_el = card.select_one(".grid-product__price-value")
        price, currency = parse_price(price_el.get_text(" ", strip=True) if price_el else None)
        img = card.select_one("img.grid-product__picture") or card.select_one("img")
        listing = Listing(id=pid, title=title, url=url, price=price, currency=currency,
                          image_url=img.get("src") if img else None)
        apply_specs(listing, title + " " + unquote(url).rsplit("/", 1)[-1].replace("-", " "))
        if listing.screen_inches is None and default_screen_inches:
            listing.screen_inches = default_screen_inches
        listings.append(listing)

    if cards and not listings:
        raise ParserError(f"{len(cards)} product cards found but none could be parsed")
    if declared_n is not None and declared_n != len(listings):
        log.warning("grid declares %s items but %d were parsed", declared_n, len(listings))
    if not listings:
        log.warning("category page has zero products (grid present, data-items=%r)", declared)
    return listings


def parse_product_page(html: str) -> dict:
    """Extract the bits of a product page useful for enrichment."""
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one(".product-details__product-title")
    price_el = soup.select_one(".product-details__product-price")
    desc_el = soup.select_one(".product-details__product-description")
    img = soup.select_one(".details-gallery img, .product-details img")
    title = " ".join(title_el.get_text(" ").split()) if title_el else None
    if title is None and desc_el is None:
        raise ParserError("product page has neither title nor description; layout may have changed")
    price, currency = parse_price(price_el.get_text(" ", strip=True) if price_el else None)
    return {
        "title": title,
        "price": price,
        "currency": currency,
        "description": " ".join(desc_el.get_text(" ").split()) if desc_el else "",
        "image_url": img.get("src") if img else None,
    }


def enrich_from_product_page(listing: Listing, html: str) -> Listing:
    """Fill fields that the category card left empty using the product page."""
    data = parse_product_page(html)
    if listing.price is None and data["price"] is not None:
        listing.price, listing.currency = data["price"], data["currency"]
    if listing.image_url is None:
        listing.image_url = data["image_url"]
    if data["title"]:
        apply_specs(listing, data["title"])
    if data["description"]:
        apply_specs(listing, data["description"])
    return listing

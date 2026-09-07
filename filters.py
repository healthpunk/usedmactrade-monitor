"""Matching rules: decide whether a normalized listing is interesting."""
from __future__ import annotations

from dataclasses import dataclass, field

from parser import Listing


@dataclass(frozen=True)
class Criteria:
    model: str | None = "MacBook Pro"
    screen_inches: int | None = 14
    min_ram_gb: int | None = 32
    max_price_usd: float | None = None
    min_ssd_gb: int | None = None
    max_battery_cycles: int | None = None
    min_battery_health_pct: int | None = None
    allowed_chips: tuple[str, ...] = ()      # empty = any chip
    preferred_chips: tuple[str, ...] = ()    # informational only
    match_unknown_fields: bool = False


@dataclass
class MatchResult:
    matched: bool
    preferred: bool = False
    reasons: list[str] = field(default_factory=list)   # why it did NOT match
    unknown: list[str] = field(default_factory=list)   # fields that were None


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def evaluate(listing: Listing, c: Criteria) -> MatchResult:
    """Apply every enabled constraint. Unknown (None) fields fail the constraint
    unless ``c.match_unknown_fields`` is set; they are always reported."""
    reasons: list[str] = []
    unknown: list[str] = []

    def check(name: str, value, ok, describe: str) -> None:
        if value is None:
            unknown.append(name)
            if not c.match_unknown_fields:
                reasons.append(f"{name} unknown ({describe})")
        elif not ok(value):
            reasons.append(f"{name}={value!r} fails {describe}")

    if c.model is not None:
        check("model", listing.model, lambda v: _norm(v) == _norm(c.model), f"model == {c.model}")
    if c.screen_inches is not None:
        check("screen_inches", listing.screen_inches, lambda v: v == c.screen_inches, f"screen == {c.screen_inches}")
    if c.min_ram_gb is not None:
        check("ram_gb", listing.ram_gb, lambda v: v >= c.min_ram_gb, f"ram >= {c.min_ram_gb}")
    if c.min_ssd_gb is not None:
        check("ssd_gb", listing.ssd_gb, lambda v: v >= c.min_ssd_gb, f"ssd >= {c.min_ssd_gb}")
    if c.max_battery_cycles is not None:
        check("battery_cycles", listing.battery_cycles, lambda v: v <= c.max_battery_cycles, f"cycles <= {c.max_battery_cycles}")
    if c.min_battery_health_pct is not None:
        check("battery_health_pct", listing.battery_health_pct, lambda v: v >= c.min_battery_health_pct, f"health >= {c.min_battery_health_pct}")
    if c.max_price_usd is not None:
        if listing.currency not in (None, "USD"):
            unknown.append("price_usd")   # can't compare other currencies; don't fail on it
        else:
            check("price", listing.price, lambda v: v <= c.max_price_usd, f"price <= {c.max_price_usd} USD")
    if c.allowed_chips:
        allowed = {_norm(x) for x in c.allowed_chips}
        check("chip", listing.chip, lambda v: _norm(v) in allowed, f"chip in {list(c.allowed_chips)}")

    preferred = bool(listing.chip) and _norm(listing.chip) in {_norm(x) for x in c.preferred_chips}
    return MatchResult(matched=not reasons, preferred=preferred, reasons=reasons, unknown=unknown)

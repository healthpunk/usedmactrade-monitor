"""Monitor configuration.

Filter values live here so the matching rule is defined in exactly one place.
Secrets are never stored here: they are read from the environment (see README).
"""
from __future__ import annotations

import os

from filters import Criteria

# --- Source -----------------------------------------------------------------
CATEGORY_URL = (
    "https://usedmactrade.com/"
    "Macbook-PRO-14-%D0%B4%D1%8E%D0%B9%D0%BC%D1%96%D0%B2-c155269751"
)
# The category page lists 14-inch MacBook Pros. Used as a fallback when a title
# does not state the screen size explicitly.
CATEGORY_DEFAULT_SCREEN_INCHES = 14

# --- Matching rule ------------------------------------------------------------
MODEL = "MacBook Pro"
SCREEN_SIZE = 14
MIN_RAM_GB = 32          # Apple ships 32/36/48/64/96/128 GB, so ">=" not "=="

# Future-proofing: leave as None / empty to disable a constraint.
MAX_PRICE_USD: float | None = None
MIN_SSD_GB: int | None = None
MAX_BATTERY_CYCLES: int | None = None
MIN_BATTERY_HEALTH_PCT: int | None = None
ALLOWED_CHIPS: list[str] = []            # e.g. ["M3 Pro", "M3 Max", "M4 Pro"]; empty = any chip
PREFERRED_CHIPS: list[str] = ["M3", "M3 Pro", "M3 Max"]   # flagged as 🔥, never used to exclude

# When a field needed by a constraint could not be parsed (None), treat the
# listing as a match anyway? False = conservative (skip + warn in logs).
MATCH_UNKNOWN_FIELDS = False

# --- Behaviour ----------------------------------------------------------------
NOTIFY_PRICE_CHANGES = False   # price changes are detected + logged, not sent (yet)
PENDING_TTL_HOURS = 72         # un-sent matches older than this are dropped, not sent late
ALERT_COOLDOWN_HOURS = 24      # at most one "parser broken" Telegram alert per day

# --- HTTP ---------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 usedmactrade-monitor/1.0"
)
HTTP_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 3
MAX_PAGES = 10                 # pagination safety cap (site shows a handful of items)

# --- Files / secrets ----------------------------------------------------------
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def criteria() -> Criteria:
    """Build the active matching rule from the constants above."""
    return Criteria(
        model=MODEL,
        screen_inches=SCREEN_SIZE,
        min_ram_gb=MIN_RAM_GB,
        max_price_usd=MAX_PRICE_USD,
        min_ssd_gb=MIN_SSD_GB,
        max_battery_cycles=MAX_BATTERY_CYCLES,
        min_battery_health_pct=MIN_BATTERY_HEALTH_PCT,
        allowed_chips=tuple(ALLOWED_CHIPS),
        preferred_chips=tuple(PREFERRED_CHIPS),
        match_unknown_fields=MATCH_UNKNOWN_FIELDS,
    )

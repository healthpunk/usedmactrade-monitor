"""Minimal Telegram Bot API client (no library needed for two endpoints)."""
from __future__ import annotations

import html
import logging
import time

import requests

from parser import Listing

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


def _fmt_price(price: float | None, currency: str | None) -> str | None:
    if price is None:
        return None
    sym = {"USD": "$", "EUR": "€", "UAH": "₴", "PLN": "zł"}.get(currency or "", "")
    amount = f"{price:,.0f}" if float(price).is_integer() else f"{price:,.2f}"
    return f"{sym}{amount}" if sym in ("$", "€") else f"{amount} {sym or currency or ''}".strip()


def format_listing_message(listing: Listing, *, preferred: bool = False,
                           header: str = "New UsedMacTrade match") -> str:
    """Compact HTML message. Only fields we actually know are included."""
    e = html.escape
    lines = [f"{'🔥' if preferred else '🆕'} <b>{e(header)}</b>"]
    model = listing.model or "MacBook"
    lines.append(e(f"{model} {listing.screen_inches}\"" if listing.screen_inches else model))
    specs = [x for x in (
        listing.chip,
        f"{listing.ram_gb} GB" if listing.ram_gb else None,
        f"{listing.ssd_gb} GB" if listing.ssd_gb else None,
    ) if x]
    if specs:
        lines.append(e(" · ".join(specs)))
    price = _fmt_price(listing.price, listing.currency)
    if price:
        lines.append(f"<b>{e(price)}</b>")
    if listing.battery_health_pct is not None:
        lines.append(e(f"Battery: {listing.battery_health_pct}%"))
    if listing.battery_cycles is not None:
        lines.append(e(f"Cycles: {listing.battery_cycles}"))
    if listing.condition:
        lines.append(e(f"Condition: {listing.condition}"))
    if listing.warranty:
        lines.append(e(listing.warranty))
    lines.append("")
    lines.append(f"<a href=\"{e(listing.url, quote=True)}\">Open listing</a>")
    lines.append(f"<i>{e(listing.title[:200])}</i>")
    return "\n".join(lines)


def format_price_change_message(old_price: float | None, listing: Listing) -> str:
    e = html.escape
    old = _fmt_price(old_price, listing.currency) or "?"
    new = _fmt_price(listing.price, listing.currency) or "?"
    return (f"💲 <b>Price change</b>\n{e(listing.title[:120])}\n{e(old)} → <b>{e(new)}</b>\n"
            f"<a href=\"{e(listing.url, quote=True)}\">Open listing</a>")


class TelegramClient:
    def __init__(self, token: str, chat_id: str, *, session: requests.Session | None = None,
                 timeout: float = 15, retries: int = 3):
        if not token or not chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.token, self.chat_id = token, str(chat_id)
        self.session = session or requests.Session()
        self.timeout, self.retries = timeout, retries

    def _call(self, method: str, payload: dict) -> dict:
        url = API.format(token=self.token, method=method)
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last = exc
                log.warning("telegram %s network error (attempt %d): %s", method, attempt, exc)
                time.sleep(attempt)
                continue
            body = {}
            try:
                body = resp.json()
            except ValueError:
                pass
            if resp.status_code == 200 and body.get("ok"):
                return body["result"]
            desc = body.get("description", resp.text[:200])
            if resp.status_code == 429:
                wait = (body.get("parameters") or {}).get("retry_after", attempt * 2)
                log.warning("telegram rate limited; sleeping %ss", wait)
                time.sleep(float(wait))
                continue
            if resp.status_code >= 500:
                last = TelegramError(f"{method} -> {resp.status_code}: {desc}")
                time.sleep(attempt)
                continue
            raise TelegramError(f"{method} -> {resp.status_code}: {desc}")   # 4xx: don't retry
        raise TelegramError(f"{method} failed after {self.retries} attempts: {last}")

    def send_message(self, text: str, *, disable_preview: bool = False) -> dict:
        return self._call("sendMessage", {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                          "disable_web_page_preview": disable_preview})

    def send_photo(self, photo_url: str, caption: str) -> dict:
        return self._call("sendPhoto", {"chat_id": self.chat_id, "photo": photo_url,
                                        "caption": caption, "parse_mode": "HTML"})

    def send_listing(self, listing: Listing, *, preferred: bool = False) -> dict:
        """Photo with caption when we have an image; falls back to a text message
        (Telegram rejects some CDN images / oversized captions)."""
        text = format_listing_message(listing, preferred=preferred)
        if listing.image_url and len(text) <= 1024:
            try:
                return self.send_photo(listing.image_url, text)
            except TelegramError as exc:
                log.warning("sendPhoto failed (%s); falling back to sendMessage", exc)
        return self.send_message(text)

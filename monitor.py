#!/usr/bin/env python3
"""UsedMacTrade new-listing monitor: fetch -> diff against state -> notify.

Run ``python monitor.py`` (see README). Exit codes: 0 ok, 1 notification
problem (state was still saved), 2 fetch/parse failure.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
import state as st
from filters import Criteria, evaluate
from parser import Listing, ParserError, enrich_from_product_page, parse_category_page
from telegram import TelegramClient, TelegramError, format_price_change_message

log = logging.getLogger("monitor")
Fetcher = Callable[[str], str]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "uk,en;q=0.8"})
    retry = Retry(total=config.HTTP_RETRIES, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def make_fetcher(session: requests.Session) -> Fetcher:
    def fetch(url: str) -> str:
        log.debug("GET %s", url)
        resp = session.get(url, timeout=config.HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.text
    return fetch


def fetch_all_listings(fetch: Fetcher, category_url: str) -> list[Listing]:
    """Walk ``?offset=N`` pages until a page adds nothing new."""
    listings: list[Listing] = []
    seen: set[str] = set()
    sep = "&" if "?" in category_url else "?"
    for _ in range(config.MAX_PAGES):
        url = category_url if not listings else f"{category_url}{sep}offset={len(listings)}"
        try:
            page = parse_category_page(fetch(url), default_screen_inches=config.CATEGORY_DEFAULT_SCREEN_INCHES)
        except ParserError:
            if not listings:
                raise          # first page unreadable: real problem
            log.debug("offset page %s has no grid; end of list", url)
            break
        fresh = [l for l in page if l.id not in seen]
        if not fresh:
            break
        listings.extend(fresh)
        seen.update(l.id for l in fresh)
    else:
        log.warning("stopped paginating after %d pages", config.MAX_PAGES)
    return listings


def enrich(listing: Listing, fetch: Fetcher) -> Listing:
    """Best-effort: pull cycles/health/etc. from the product page for new items."""
    try:
        return enrich_from_product_page(listing, fetch(listing.url))
    except (requests.RequestException, ParserError) as exc:
        log.warning("could not enrich %s from product page: %s", listing.id, exc)
        return listing


# --------------------------------------------------------------------------- #
# Core run
# --------------------------------------------------------------------------- #
def run_once(*, fetch: Fetcher, notifier: TelegramClient | None, criteria: Criteria,
             state_path: str = config.STATE_PATH, category_url: str = config.CATEGORY_URL,
             dry_run: bool = False, now: datetime | None = None) -> int:
    now_dt = now or datetime.now(timezone.utc)
    now_s = now_dt.replace(microsecond=0).isoformat()
    state = st.load_state(state_path)
    first_run = st.is_first_run(state)
    before = st.digest(state)
    # Changes once a month -> one tiny commit/month, which keeps GitHub from
    # disabling the scheduled workflow after 60 days of repo inactivity.
    state["heartbeat"] = now_dt.strftime("%Y-%m")

    def save() -> None:
        if dry_run:
            return
        if before != st.digest(state) or not os.path.exists(state_path):
            st.save_state(state_path, state)
            log.info("state saved to %s", state_path)
        else:
            log.info("state unchanged; not rewritten")

    try:
        listings = fetch_all_listings(fetch, category_url)
    except (requests.RequestException, ParserError) as exc:
        log.error("fetch/parse failed: %s", exc)
        _maybe_alert(state, notifier, f"⚠️ usedmactrade-monitor: fetch/parse failed: {exc}", now_dt, dry_run)
        save()
        return 2

    log.info("fetched %d listing(s) from %s", len(listings), category_url)
    for l in listings:
        log.info("  %s | %s | %s %s | %s | %s | ram=%s ssd=%s cycles=%s health=%s",
                 l.id, l.title[:70], l.price, l.currency, l.model, l.chip,
                 l.ram_gb, l.ssd_gb, l.battery_cycles, l.battery_health_pct)

    if first_run:
        log.info("first run: recording %d listing(s) as baseline, sending nothing", len(listings))
        st.record_listings(state, listings, status_for_new=st.STATUS_BASELINE, now=now_s)
        for l in listings:
            entry = state["listings"][l.id]
            res = evaluate(l, criteria)
            entry["match_reasons"] = res.reasons
            log.info("  baseline %s: %s", l.id, "MATCH" if res.matched else f"no match ({'; '.join(res.reasons)})")
        save()
        return 0

    changes = st.diff_listings(state, listings)
    log.info("new=%d price_changed=%d", len(changes.new), len(changes.price_changed))

    # Evaluate genuinely new listings (enriched from their product page).
    for listing in changes.new:
        enrich(listing, fetch)
    st.record_listings(state, listings, status_for_new=st.STATUS_PENDING, now=now_s)
    for listing in changes.new:
        entry = state["listings"][listing.id]
        res = evaluate(listing, criteria)
        entry["match_reasons"] = res.reasons
        if res.matched:
            log.info("NEW MATCH %s: %s", listing.id, listing.title)
            entry["preferred"] = res.preferred
        else:
            entry["status"] = st.STATUS_NO_MATCH
            log.info("new but not a match %s: %s", listing.id, "; ".join(res.reasons))
        if res.unknown:
            log.warning("listing %s has unparsed fields: %s", listing.id, ", ".join(res.unknown))

    for old, listing in changes.price_changed:
        log.info("price change %s: %s -> %s %s", listing.id, old.get("price"), listing.price, listing.currency)
        if config.NOTIFY_PRICE_CHANGES and notifier and not dry_run:
            try:
                notifier.send_message(format_price_change_message(old.get("price"), listing))
            except TelegramError as exc:
                log.error("price-change notification failed: %s", exc)

    # Send everything still pending (new matches + earlier ones Telegram rejected).
    exit_code = 0
    for entry in st.pending_entries(state):
        first_seen = datetime.fromisoformat(entry["first_seen"])
        if now_dt - first_seen > timedelta(hours=config.PENDING_TTL_HOURS):
            entry["status"] = st.STATUS_EXPIRED
            log.warning("dropping stale pending match %s (first seen %s)", entry["id"], entry["first_seen"])
            continue
        listing = Listing.from_dict(entry)
        if dry_run:
            log.info("[dry-run] would notify: %s", listing.title)
            continue
        if notifier is None:
            log.error("match pending but Telegram is not configured: %s", listing.title)
            exit_code = 1
            continue
        try:
            notifier.send_listing(listing, preferred=bool(entry.get("preferred")))
            entry["status"], entry["notified_at"] = st.STATUS_SENT, now_s
            log.info("notified %s", listing.id)
        except TelegramError as exc:
            log.error("notification failed for %s: %s", listing.id, exc)
            exit_code = 1

    save()
    return exit_code


def _maybe_alert(state: dict, notifier: TelegramClient | None, text: str, now: datetime, dry_run: bool) -> None:
    """Tell the user the monitor is broken, at most once per cooldown window."""
    if notifier is None or dry_run:
        return
    last = state.get("last_alert_at")
    if last and now - datetime.fromisoformat(last) < timedelta(hours=config.ALERT_COOLDOWN_HOURS):
        return
    try:
        notifier.send_message(text, disable_preview=True)
        state["last_alert_at"] = now.replace(microsecond=0).isoformat()
    except TelegramError as exc:
        log.error("could not send alert: %s", exc)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch + evaluate; no Telegram, no state write")
    ap.add_argument("--test-telegram", action="store_true", help="send a test message and exit")
    ap.add_argument("--state", default=config.STATE_PATH, help="path to state file")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    session = build_session()
    notifier = None
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        notifier = TelegramClient(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, session=session)
    else:
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; notifications disabled")

    if args.test_telegram:
        if notifier is None:
            log.error("cannot test: Telegram credentials missing")
            return 1
        notifier.send_message("✅ usedmactrade-monitor: Telegram is configured correctly.")
        log.info("test message sent")
        return 0

    return run_once(fetch=make_fetcher(session), notifier=notifier, criteria=config.criteria(),
                    state_path=args.state, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

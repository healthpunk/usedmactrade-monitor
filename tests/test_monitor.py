"""End-to-end behaviour with a fake site and a fake Telegram."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import state as st
from filters import Criteria
from monitor import run_once
from telegram import TelegramError

CRIT = Criteria(model="MacBook Pro", screen_inches=14, min_ram_gb=32, preferred_chips=("M3 Pro",))
CAT = "https://example.test/cat-c1"


def card(pid, title, price="$1 790.00"):
    return f'''<div class="grid-product grid-product--id-{pid}"><div class="grid-product__wrap" data-product-id="{pid}">
    <a href="https://example.test/{pid}-p{pid}" class="grid-product__image"><img class="grid-product__picture" src="https://img.test/{pid}.jpg"></a>
    <a href="https://example.test/{pid}-p{pid}" class="grid-product__title"><div class="grid-product__title-inner">{title}</div></a>
    <div class="grid-product__price"><div class="grid-product__price-value">{price}</div></div></div></div>'''


def page(cards):
    return f'<html><body><div class="grid__products" data-items="{len(cards)}">{"".join(cards)}</div></body></html>'


class FakeSite:
    def __init__(self):
        self.inventory = {}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url.startswith(CAT):
            offset = int(url.split("offset=")[1]) if "offset=" in url else 0
            cards = [card(pid, *rest) for pid, rest in list(self.inventory.items())[offset:]]
            return page(cards)
        pid = url.rsplit("-p", 1)[1]
        title = self.inventory[pid][0]
        return f'<div class="product-details"><h1 class="product-details__product-title">{title}</h1><div class="product-details__product-description">Батарея 42 цикли, 97%</div></div>'


class FakeTelegram:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_listing(self, listing, preferred=False):
        if self.fail:
            raise TelegramError("boom")
        self.sent.append((listing.id, preferred))

    def send_message(self, text, disable_preview=False):
        self.sent.append(("msg", text))


@pytest.fixture
def env(tmp_path):
    return FakeSite(), FakeTelegram(), str(tmp_path / "state.json")


def run(site, tg, path, now=None):
    return run_once(fetch=site, notifier=tg, criteria=CRIT, state_path=path, category_url=CAT, now=now)


def test_first_run_records_baseline_without_notifying(env):
    site, tg, path = env
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 36GB RAM 1TB",), "2": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    assert run(site, tg, path) == 0
    state = json.load(open(path))
    assert set(state["listings"]) == {"1", "2"}
    assert all(e["status"] == "baseline" for e in state["listings"].values())
    assert tg.sent == []   # even though listing 1 matches


def test_new_matching_listing_notifies_once(env):
    site, tg, path = env
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    run(site, tg, path)
    site.inventory["2"] = ("MacBook Pro 14 M3 Pro 36GB RAM 1TB",)
    site.inventory["3"] = ("MacBook Pro 14 M4 24GB RAM 1TB",)      # new but no match
    assert run(site, tg, path) == 0
    assert tg.sent == [("2", True)]
    # enrichment fetched the product page of new listings only
    assert sum(1 for u in site.calls if "-p2" in u) == 1 and not any("-p1" in u for u in site.calls)
    state = json.load(open(path))
    assert state["listings"]["2"]["status"] == "sent"
    assert state["listings"]["2"]["battery_cycles"] == 42 and state["listings"]["2"]["battery_health_pct"] == 97
    assert state["listings"]["3"]["status"] == "no_match"
    # third run: nothing new -> no duplicate
    assert run(site, tg, path) == 0
    assert tg.sent == [("2", True)]


def test_price_change_is_not_a_new_listing(env):
    site, tg, path = env
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 36GB RAM 1TB", "$1 990.00")}
    run(site, tg, path)
    site.inventory["1"] = ("MacBook Pro 14 M3 Pro 36GB RAM 1TB", "$1 790.00")
    assert run(site, tg, path) == 0
    assert tg.sent == []
    assert json.load(open(path))["listings"]["1"]["price"] == 1790.0


def test_telegram_failure_keeps_match_pending_then_sends(env):
    site, tg, path = env
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    run(site, tg, path)
    site.inventory["2"] = ("MacBook Pro 14 M3 Max 48GB RAM 1TB",)
    broken = FakeTelegram(fail=True)
    assert run(site, broken, path) == 1
    assert json.load(open(path))["listings"]["2"]["status"] == "pending"
    assert run(site, tg, path) == 0
    assert tg.sent == [("2", False)]


def test_no_telegram_configured_returns_1_but_saves_state(env):
    site, _, path = env
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    run(site, None, path)
    site.inventory["2"] = ("MacBook Pro 14 M3 Max 48GB RAM 1TB",)
    assert run(site, None, path) == 1
    assert json.load(open(path))["listings"]["2"]["status"] == "pending"


def test_stale_pending_expires(env):
    site, tg, path = env
    t0 = datetime(2026, 9, 7, tzinfo=timezone.utc)
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    run(site, tg, path, now=t0)
    site.inventory["2"] = ("MacBook Pro 14 M3 Max 48GB RAM 1TB",)
    run(site, None, path, now=t0 + timedelta(minutes=10))
    assert run(site, tg, path, now=t0 + timedelta(days=5)) == 0
    assert tg.sent == []
    assert json.load(open(path))["listings"]["2"]["status"] == "expired"


def test_parser_failure_returns_2_and_alerts_once(env):
    _, tg, path = env
    site = FakeSite(); site.inventory = {"1": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    run(site, tg, path)
    broken = lambda url: "<html><body>maintenance</body></html>"
    assert run_once(fetch=broken, notifier=tg, criteria=CRIT, state_path=path, category_url=CAT) == 2
    assert run_once(fetch=broken, notifier=tg, criteria=CRIT, state_path=path, category_url=CAT) == 2
    assert len([s for s in tg.sent if s[0] == "msg"]) == 1   # cooldown


def test_pagination_walks_offsets(env):
    site, tg, path = env
    site.inventory = {str(i): (f"MacBook Pro 14 M3 Pro 18GB RAM 512GB #{i}",) for i in range(3)}
    run(site, tg, path)
    assert len(json.load(open(path))["listings"]) == 3


def test_dedup_state_helpers(tmp_path):
    from parser import Listing
    s = st.empty_state()
    a = Listing(id="a", title="t", url="u", price=1.0)
    st.record_listings(s, [a], status_for_new=st.STATUS_BASELINE, now="2026-09-07T00:00:00+00:00")
    b = Listing(id="b", title="t", url="u", price=2.0)
    a2 = Listing(id="a", title="t", url="u", price=5.0)
    ch = st.diff_listings(s, [a2, b])
    assert [l.id for l in ch.new] == ["b"] and [l.id for _, l in ch.price_changed] == ["a"]
    assert st.diff_listings(s, [b]).gone == ["a"]          # 'a' was present at the last check
    st.record_listings(s, [b], status_for_new=st.STATUS_PENDING, now="2026-09-07T00:10:00+00:00")
    assert st.diff_listings(s, [b]).gone == []             # already accounted for
    assert st.digest(s) != st.digest(st.empty_state())
    p = tmp_path / "s.json"
    st.save_state(str(p), s)
    assert st.load_state(str(p)) == s


def test_state_not_rewritten_when_nothing_changed(env):
    import os
    site, tg, path = env
    site.inventory = {"1": ("MacBook Pro 14 M3 Pro 18GB RAM 512GB",)}
    t0 = datetime(2026, 9, 7, tzinfo=timezone.utc)
    run(site, tg, path, now=t0)
    mtime = os.stat(path).st_mtime_ns
    run(site, tg, path, now=t0 + timedelta(minutes=10))
    assert os.stat(path).st_mtime_ns == mtime           # same month, same inventory
    run(site, tg, path, now=t0 + timedelta(days=40))
    assert os.stat(path).st_mtime_ns != mtime           # heartbeat month rolled over

# usedmactrade-monitor

Watches the [14-inch MacBook Pro category](https://usedmactrade.com/Macbook-PRO-14-%D0%B4%D1%8E%D0%B9%D0%BC%D1%96%D0%B2-c155269751)
on UsedMacTrade every ~10 minutes and sends a Telegram message when a **new**
listing matches the filter (MacBook Pro · 14" · RAM ≥ 32 GB). Each listing is
notified at most once.

## Architecture

```
GitHub Actions cron (*/10)  ──►  python monitor.py  ──►  Telegram Bot API
                                      │  ▲
                                      ▼  │
                                  state.json  (committed back to the repo)
```

- **No server, no database.** One Python script, two dependencies
  (`requests`, `beautifulsoup4`).
- **State** is `state.json`, committed by the workflow after each run that
  changes it. GitHub Actions runners are ephemeral; the repo is the durable
  store. A `concurrency` group prevents two runs from racing on the file.
- Nothing is sent on the very first run: existing inventory is recorded as the
  baseline. The baseline in this repo was created locally on 2026-09-07.

| File | Role |
|---|---|
| `monitor.py` | fetch → diff against state → evaluate → notify → save |
| `parser.py`  | Ecwid grid/product-page parsing, spec extraction regexes |
| `filters.py` | `Criteria` + `evaluate()` matching rules |
| `state.py`   | JSON state, dedup, change detection |
| `telegram.py`| tiny Bot API client + message formatting |
| `config.py`  | **the only place filters/URLs are defined** |

## How the site is parsed

UsedMacTrade is an Ecwid storefront that server-renders the product grid, so a
plain HTTP GET is enough (no JS rendering, no browser). Each card is
`.grid-product` with `data-product-id`, a title, a price and an image; more
items are fetched with `?offset=N` until a page adds nothing new.

Specs (chip, RAM, SSD, cycles, battery %, warranty, condition) live only in
free-text titles like `MACBOOK PRO 14 M4 PRO / 24GB RAM / SSD 512B / лише 30
циклів / APPLE CARE 07/2027`. `parser.extract_specs()` uses anchored regexes
(RAM needs a RAM/ОЗУ/оперативн anchor, SSD needs SSD/диск, …) that tolerate
Ukrainian/English mixes and typos. For **new** listings only, the product page
is fetched too and its description fills any still-missing fields. Anything not
found stays `null`; nothing is guessed.

If the grid container is missing from a successfully loaded first page, the run
fails loudly (exit 2) and, when Telegram is configured, sends one ⚠️ alert per
24 h. A grid that is present but empty is treated as genuinely empty.

## How matching works

`config.py`:

```python
MODEL = "MacBook Pro"
SCREEN_SIZE = 14
MIN_RAM_GB = 32                     # >= : 32, 36, 48, 64 … all match
MAX_PRICE_USD = None                # future knobs; None = disabled
MIN_SSD_GB = None
MAX_BATTERY_CYCLES = None
MIN_BATTERY_HEALTH_PCT = None
ALLOWED_CHIPS = []                  # e.g. ["M3 Pro", "M3 Max", "M4 Pro"]
PREFERRED_CHIPS = ["M3", "M3 Pro", "M3 Max"]   # only adds a 🔥 to the message
MATCH_UNKNOWN_FIELDS = False        # a listing with unparsed RAM is NOT a match
```

Every enabled constraint must pass. Change a value, commit, done. Matching is
applied to new listings only; a price change on a known listing is detected and
logged but not sent (`NOTIFY_PRICE_CHANGES = True` turns it on).

Un-sent matches stay `pending` in the state and are retried on the next run
(e.g. Telegram outage or credentials not yet set); after `PENDING_TTL_HOURS`
(72) they expire instead of arriving late.

## Telegram setup

1. In Telegram, talk to **@BotFather** → `/newbot` → copy the token
   (`123456:ABC-…`).
2. Send your new bot any message (it can only write to chats that contacted
   it), then get your chat id:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -c 'import sys,json;print({u["message"]["chat"]["id"] for u in json.load(sys.stdin)["result"] if "message" in u})'
   ```
3. Store both as repository secrets:
   ```bash
   gh secret set TELEGRAM_BOT_TOKEN --repo healthpunk/usedmactrade-monitor
   gh secret set TELEGRAM_CHAT_ID   --repo healthpunk/usedmactrade-monitor
   ```
4. Verify: `gh workflow run monitor.yml --repo healthpunk/usedmactrade-monitor`,
   or locally `TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… python monitor.py --test-telegram`.

### Required secrets

| Secret | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather |
| `TELEGRAM_CHAT_ID`   | Chat/user id that receives alerts |

Credentials are read from the environment only. Without them the monitor still
runs, records inventory and exits 1 when it has something to send.

## Running manually

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python monitor.py --dry-run -v      # fetch + evaluate, touch nothing
python monitor.py                   # real run against ./state.json
python -m pytest -q                 # tests (offline, uses tests/fixtures)
```

Exit codes: `0` ok · `1` notification problem (state still saved) · `2` site
fetch/parse failure.

## Scheduled monitoring

`.github/workflows/monitor.yml` runs on `*/10 * * * *` (GitHub can delay cron
runs by a few minutes) and on manual dispatch. It runs the script with the
secrets, then commits `state.json` if it changed. Only `state.json` commits are
made by the bot, and only when inventory actually changes, so the history stays
small. `tests.yml` runs pytest on every non-state push.

> GitHub disables cron workflows after 60 days without repository activity.
> The bot's own state commits count as activity whenever inventory changes;
> if the shop is frozen for two months, re-enable it from the Actions tab.

## Changing filters / extending

- Filters: edit `config.py`. New constraint types: add a field to
  `filters.Criteria` and a `check()` line in `evaluate()`.
- Price alerts: flip `NOTIFY_PRICE_CHANGES`; `telegram.format_price_change_message`
  already exists.
- Another shop (OLX, Allegro, …): write a `parse_category_page`-style function
  returning `parser.Listing` objects and point `fetch_all_listings` at it; state,
  matching and Telegram are source-agnostic.

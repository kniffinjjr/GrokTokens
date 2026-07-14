# GrokTokens

**Local live dashboard** for [Grok](https://x.ai) / [Grok Build](https://x.ai/news/grok-build-cli) token usage across agents and sessions on your machine.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
![Python 3](https://img.shields.io/badge/python-3.10%2B-yellow.svg)

- Reads only local Grok session files under `~/.grok` (or `$GROK_HOME`)
- Binds to **`127.0.0.1` only** — no cloud, no telemetry from this app
- Estimates **USD** from published [xAI API rates](https://docs.x.ai/developers/pricing)
- Supports **subscription plans** (e.g. X Premium+, SuperGrok) as **API-equivalent value**, not invoices
- Optional soft **budget** bar and approximate **prompt rate-limit** proxy

> **Not affiliated with xAI.** Token totals and dollars are **estimates** from local `turn_completed` usage. They are **not** your official bill. Subscription users are not charged per token the same way as the public API.

---

## Requirements

- **Windows 10/11**
- **Python 3.10+** with `pythonw.exe` (recommended) or `python.exe`
- [Grok CLI / Grok Build](https://x.ai/cli) installed so sessions exist under `~/.grok`

No pip packages required (stdlib only).

---

## Quick start

```text
1. Copy config.example.json → config.json  (or run Start once; it will copy for you)
2. Double-click Start-GrokTokens.bat
   (or Start-GrokTokens.vbs)
3. Open http://127.0.0.1:8765/
```

**Stop:** double-click `Stop-GrokTokens.bat` / `Stop-GrokTokens.vbs`.

### From a terminal

```powershell
cd path\to\GrokTokens
copy config.example.json config.json   # first time
pythonw server.py
# then open http://127.0.0.1:8765/
```

Optional env vars:

| Variable | Default | Meaning |
|----------|---------|---------|
| `GROKTOKENS_PORT` | `8765` | HTTP port |
| `GROK_HOME` | `~/.grok` | Grok data directory |

---

## Configuration

Edit **`config.json`** (gitignored). Start from **`config.example.json`**.

### API pay-as-you-go

```json
"plan": { "name": "API pay-as-you-go", "billing": "api", "monthlyUsd": 0 },
"limits": { "period": "month", "budgetUsd": 100, "softWarnPercent": 80 }
```

### Subscription (Premium+ / SuperGrok)

```json
"plan": {
  "name": "X Premium+",
  "billing": "subscription",
  "monthlyUsd": 40
},
"limits": {
  "period": "month",
  "budgetUsd": 40,
  "softWarnPercent": 100
},
"rateLimits": {
  "enabled": true,
  "windowHours": 2,
  "maxPrompts": 100
}
```

- **`budgetUsd`**: soft bar (for subs, often set equal to monthly plan fee).
- **`rateLimits`**: coarse proxy using **active session turn counts** vs a prompt cap. Community-reported chat caps vary; **Grok Build may differ**. Tune to taste.
- **`pricing.models`**: USD per **1M tokens** (input / cached input / output). Update when xAI changes list prices.

---

## What the numbers mean

| Metric | Source |
|--------|--------|
| Token totals | Sum of `usage` on `turn_completed` events in each session's `updates.jsonl` |
| Input / cache / output | Same; **input** is treated as full prompt size (includes cache hits) |
| Estimated $ | `(uncached×inRate + cache×cacheRate + out×outRate) / 1e6` |
| Context % | `signals.json` current window fill (not cumulative spend) |
| Active agents | `~/.grok/active_sessions.json` + process check |

**Not included:** server-side tool fees (e.g. web search), image/video, official console balance, or unpublished plan quotas.

---

## Privacy & safety

- Serves **localhost only**
- Does **not** read `auth.json` or send data off-machine
- Does **not** spawn console tools on each refresh (no `tasklist` / PowerShell on poll)
- Keep `config.json` local if it contains anything you consider private

---

## Project layout

```text
GrokTokens/
  server.py              # HTTP API + collectors
  dashboard.html         # Live UI (polls /api/state)
  config.example.json    # Template (commit this)
  config.json            # Your local settings (gitignored)
  Start-GrokTokens.vbs   # Silent start
  Stop-GrokTokens.vbs    # Silent stop
  Start-GrokTokens.bat
  Stop-GrokTokens.bat
  LICENSE                # MIT
  README.md
```

### API endpoints

| Path | Description |
|------|-------------|
| `GET /` | Dashboard |
| `GET /api/health` | `{ "ok": true, "pid": ... }` |
| `GET /api/state` | Full snapshot (agents, totals, period, budget, plan, rateLimit, pricing) |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Offline / cannot connect | Run `Start-GrokTokens.bat`; confirm nothing else uses the port |
| `pythonw` not found | Install Python from python.org and enable PATH |
| Wrong home directory | Set `GROK_HOME` to your Grok data root |
| Black window flashes every few seconds | Use latest `server.py` (old versions called `tasklist` every poll) |
| $ looks wrong | Adjust `pricing` in `config.json`; remember subscription is not API billing |

---

## Disclaimer

Provided **as-is** under the MIT License. Unofficial community tool. Session file formats may change with Grok updates. Always verify billing in the official xAI / X product UI.

---

## License

[MIT](LICENSE)

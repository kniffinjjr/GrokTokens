# GrokTokens (Multi-Provider)

**Local live dashboard** for token usage and estimated costs across AI coding agents.

Originally built for [Grok](https://x.ai) / [Grok Build](https://x.ai/news/grok-build-cli). Now supports a multi-provider architecture so you can add Cursor, Anthropic/Claude, OpenAI, and others.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
![Python 3](https://img.shields.io/badge/python-3.10%2B-yellow.svg)

- Reads only **local** session/log files — no cloud, no telemetry
- Binds to **`127.0.0.1` only**
- Tracks the **active model** per agent and applies the correct published rates
- Estimates **USD** from published provider rates
- Supports **subscription plans** as API-equivalent value

> **Not affiliated with xAI, Anthropic, OpenAI, or Cursor.** Token totals and dollars are **estimates** from local data. They are **not** your official bill.

---

## What's New (Multi-Provider)

- `config.json` now has a `providers` array. Enable/disable Grok, Cursor, Anthropic, OpenAI independently.
- Every agent reports its **active model** (from session metadata or logs). Pricing uses that model.
- Dashboard shows provider cards with active model badges.
- Grok collector is fully functional. Cursor / Anthropic / OpenAI are **stubs** ready for you to point at log paths and extend the parsers.

---

## Requirements

- **Windows 10/11**
- **Python 3.10+** with `pythonw.exe` (recommended) or `python.exe`
- Grok CLI / Grok Build installed so sessions exist under `~/.grok` (for the Grok provider)

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

### Providers

```json
"providers": [
  {
    "id": "grok",
    "name": "Grok / Grok Build",
    "type": "grok",
    "enabled": true,
    "home": "~/.grok"
  },
  {
    "id": "cursor",
    "name": "Cursor",
    "type": "cursor",
    "enabled": false,
    "logsPath": "%APPDATA%/Cursor/User/globalStorage"
  }
]
```

- **Active model tracking**: Grok reads `current_model_id` / `primaryModelId` from session files. Other providers should extract the model string from their logs so pricing is accurate.
- Pricing table under `pricing.models` already includes common Claude and GPT rates. Add more as needed.

### Plan / Budget / Rate limits

Same as before — see comments in `config.example.json`.

---

## What the numbers mean

| Metric | Source |
|--------|--------|
| Token totals | Per-turn usage (Grok: `turn_completed` in `updates.jsonl`) |
| **Active model** | Session summary / signals (Grok) or log parsing (others) |
| Estimated $ | Rates for the *active model* (or per-model breakdown) |
| Context % | Current window fill |
| Active agents | Provider-specific active session lists + process check |

**Not included:** server-side tool fees, image/video generation, official console balances, or unpublished plan quotas.

---

## Privacy & safety

- Serves **localhost only**
- Does **not** read auth tokens or send data off-machine
- Does **not** spawn console tools on each refresh
- Keep `config.json` local if it contains private paths

---

## Project layout

```text
GrokTokens/
  server.py              # HTTP API + multi-provider collectors
  dashboard.html         # Live UI (polls /api/state)
  config.example.json    # Template with providers + model rates
  config.json            # Your local settings (gitignored)
  Start-GrokTokens.vbs   # Silent start
  Stop-GrokTokens.vbs    # Silent stop
  ...
```

### API endpoints

| Path | Description |
|------|-------------|
| `GET /` | Dashboard |
| `GET /api/health` | `{ "ok": true, "pid": ... }` |
| `GET /api/state` | Full snapshot (providers, agents with active models, totals, budget, ...) |

---

## Extending a provider (Cursor / Anthropic / OpenAI)

1. Set `"enabled": true` and the correct `logsPath` in `config.json`.
2. Implement a collector in `server.py` (see `collect_stub_provider`) that:
   - Scans the logs / usage files
   - Builds agent snapshots with a real `model` field (the **active** model)
   - Returns the same shape as Grok agents
3. Restart the server.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Offline / cannot connect | Run `Start-GrokTokens.bat`; confirm nothing else uses the port |
| `pythonw` not found | Install Python from python.org and enable PATH |
| Wrong home directory | Set `GROK_HOME` or edit provider `home` / `logsPath` |
| $ looks wrong | Check the **active model** badge and the rates in `pricing.models` |
| Other providers empty | They are stubs — enable + implement log parsers |

---

## Disclaimer

Provided **as-is** under the MIT License. Unofficial community tool. Session/log formats may change. Always verify billing in the official product UIs.

---

## License

[MIT](LICENSE)

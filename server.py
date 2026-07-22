#!/usr/bin/env python3
"""GrokTokens / MultiTokens local dashboard server.

Supports multiple AI agent providers (Grok, Cursor, Anthropic, OpenAI, etc.)
with per-agent active model tracking and accurate pricing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PORT = int(os.environ.get("GROKTOKENS_PORT", "8765"))
HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parent
GROK_HOME = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))
SESSIONS_ROOT = GROK_HOME / "sessions"
ACTIVE_PATH = GROK_HOME / "active_sessions.json"
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = ROOT / "GrokTokens.log"
PID_PATH = ROOT / "GrokTokens.pid"
CRASH_PATH = ROOT / "GrokTokens.crash.log"

# Default config (deep-merged with config.json / config.example.json)
_DEFAULT_CONFIG: dict[str, Any] = {
    "plan": {
        "name": "API pay-as-you-go",
        "billing": "api",
        "monthlyUsd": 0,
        "notes": [],
    },
    "limits": {
        "period": "month",
        "budgetUsd": 100.0,
        "softWarnPercent": 80,
    },
    "rateLimits": {
        "enabled": False,
        "windowHours": 2,
        "maxPrompts": 100,
    },
    "providers": [
        {
            "id": "grok",
            "name": "Grok / Grok Build",
            "type": "grok",
            "enabled": True,
            "home": "~/.grok",
        }
    ],
    "pricing": {
        "currency": "USD",
        "source": "https://docs.x.ai/developers/pricing",
        "default": {
            "inputPerM": 2.0,
            "cachedInputPerM": 0.5,
            "outputPerM": 6.0,
        },
        "models": {
            "grok-4.5": {"inputPerM": 2.0, "cachedInputPerM": 0.5, "outputPerM": 6.0},
            "grok-4.3": {"inputPerM": 1.25, "cachedInputPerM": 0.2, "outputPerM": 2.5},
            "grok-build-0.1": {"inputPerM": 1.0, "cachedInputPerM": 0.2, "outputPerM": 2.0},
        },
    },
}
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

_usage_cache: dict[str, dict[str, Any]] = {}
_session_index: dict[str, str] = {}
_index_built_at = 0.0
_history: list[dict[str, Any]] = []
_MAX_HISTORY = 180
_server_started = datetime.now(timezone.utc)


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy
    raw = read_json(CONFIG_PATH)
    if raw is None:
        example = ROOT / "config.example.json"
        raw = read_json(example)
        if raw is not None and not CONFIG_PATH.is_file():
            try:
                CONFIG_PATH.write_text(
                    example.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except OSError:
                pass
    if isinstance(raw, dict):
        cfg = deep_merge(cfg, raw)
    return cfg


def rates_for_model(cfg: dict[str, Any], model_id: str) -> dict[str, float]:
    """Return pricing rates for the *active* model. Fuzzy prefix match supported."""
    pricing = cfg.get("pricing") or {}
    default = dict(pricing.get("default") or {})
    models = pricing.get("models") or {}
    mid = (model_id or "").strip()
    if mid in models and isinstance(models[mid], dict):
        r = dict(default)
        r.update(models[mid])
        return {
            "inputPerM": float(r.get("inputPerM") or 0),
            "cachedInputPerM": float(r.get("cachedInputPerM") or 0),
            "outputPerM": float(r.get("outputPerM") or 0),
        }
    for key, val in models.items():
        if mid.startswith(str(key)) or str(key).startswith(mid):
            if isinstance(val, dict):
                r = dict(default)
                r.update(val)
                return {
                    "inputPerM": float(r.get("inputPerM") or 0),
                    "cachedInputPerM": float(r.get("cachedInputPerM") or 0),
                    "outputPerM": float(r.get("outputPerM") or 0),
                }
    return {
        "inputPerM": float(default.get("inputPerM") or 0),
        "cachedInputPerM": float(default.get("cachedInputPerM") or 0),
        "outputPerM": float(default.get("outputPerM") or 0),
    }


def estimate_cost_usd(
    cfg: dict[str, Any],
    *,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    model_id: str = "grok-4.5",
    by_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate USD using the *active model* rates (or per-model breakdown)."""
    if by_model:
        total = 0.0
        parts: list[dict[str, Any]] = []
        sum_in = sum_cch = sum_out = 0
        for mid, m in by_model.items():
            if not isinstance(m, dict):
                continue
            sub = estimate_cost_usd(
                cfg,
                input_tokens=int(m.get("inputTokens") or 0),
                cached_tokens=int(m.get("cachedReadTokens") or 0),
                output_tokens=int(m.get("outputTokens") or 0),
                model_id=str(mid),
                by_model=None,
            )
            total += float(sub["costUsd"])
            sum_in += int(m.get("inputTokens") or 0)
            sum_cch += int(m.get("cachedReadTokens") or 0)
            sum_out += int(m.get("outputTokens") or 0)
            parts.append({"model": mid, **sub})
        return {
            "costUsd": round(total, 6),
            "uncachedInputTokens": max(0, sum_in - sum_cch),
            "cachedInputTokens": sum_cch,
            "outputTokens": sum_out,
            "rates": None,
            "byModel": parts,
            "isEstimate": True,
        }

    rates = rates_for_model(cfg, model_id)
    cch = max(0, int(cached_tokens))
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    uncached = max(0, inp - cch)
    cost = (
        (uncached / 1_000_000.0) * rates["inputPerM"]
        + (cch / 1_000_000.0) * rates["cachedInputPerM"]
        + (out / 1_000_000.0) * rates["outputPerM"]
    )
    return {
        "costUsd": round(cost, 6),
        "uncachedInputTokens": uncached,
        "cachedInputTokens": cch,
        "outputTokens": out,
        "rates": rates,
        "model": model_id,
        "isEstimate": True,
    }


def period_window(period: str) -> tuple[datetime | None, str]:
    now = datetime.now(timezone.utc)
    p = (period or "month").lower()
    if p in ("none", "off", "disabled", ""):
        return None, "none"
    if p == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, "day"
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, "month"


def read_json(path: Path) -> Any | None:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def expand_path(p: str) -> Path:
    """Expand ~ and %APPDATA% style paths."""
    p = os.path.expandvars(os.path.expanduser(p or ""))
    return Path(p)


def rebuild_session_index(force: bool = False) -> None:
    global _session_index, _index_built_at
    now = time.time()
    if not force and _session_index and (now - _index_built_at) < 30:
        return
    mapping: dict[str, str] = {}
    if SESSIONS_ROOT.is_dir():
        for dirpath, dirnames, _filenames in os.walk(SESSIONS_ROOT):
            for d in list(dirnames):
                if UUID_RE.match(d):
                    mapping[d] = str(Path(dirpath) / d)
    _session_index = mapping
    _index_built_at = now


def find_session_dir(session_id: str) -> Path | None:
    rebuild_session_index()
    p = _session_index.get(session_id)
    if p and Path(p).is_dir():
        return Path(p)
    rebuild_session_index(force=True)
    p = _session_index.get(session_id)
    return Path(p) if p and Path(p).is_dir() else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def process_rss(pid: int) -> int:
    if not pid_alive(pid):
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return 0
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                return int(counters.WorkingSetSize)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass
    return 0


def get_usage_from_updates(updates_path: Path) -> dict[str, Any]:
    empty = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cachedReadTokens": 0,
        "reasoningTokens": 0,
        "totalTokens": 0,
        "modelCalls": 0,
        "turnUsageCount": 0,
        "byModel": {},
        "lastTurn": None,
    }
    if not updates_path.is_file():
        return empty

    key = str(updates_path)
    try:
        size = updates_path.stat().st_size
    except OSError:
        return empty

    cache = _usage_cache.get(key)
    if cache is None or cache.get("length", 0) > size:
        cache = {
            "length": 0,
            "pos": 0,
            "input": 0,
            "output": 0,
            "cached": 0,
            "reasoning": 0,
            "total": 0,
            "modelCalls": 0,
            "turns": 0,
            "byModel": {},
            "lastTurn": None,
        }

    if cache["length"] == size and cache["pos"] > 0:
        return {
            "inputTokens": cache["input"],
            "outputTokens": cache["output"],
            "cachedReadTokens": cache["cached"],
            "reasoningTokens": cache["reasoning"],
            "totalTokens": cache["total"],
            "modelCalls": cache["modelCalls"],
            "turnUsageCount": cache["turns"],
            "byModel": cache["byModel"],
            "lastTurn": cache["lastTurn"],
        }

    try:
        with updates_path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(cache["pos"])
            while True:
                line = f.readline()
                if not line:
                    break
                if "turn_completed" not in line or '"usage"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                upd = (obj.get("params") or {}).get("update") or {}
                if upd.get("sessionUpdate") != "turn_completed":
                    continue
                u = upd.get("usage") or {}
                if not u:
                    continue

                inp = int(u.get("inputTokens") or 0)
                out = int(u.get("outputTokens") or 0)
                cch = int(u.get("cachedReadTokens") or 0)
                rsn = int(u.get("reasoningTokens") or 0)
                tot = int(u.get("totalTokens") or 0) or (inp + out)
                mc = int(u.get("modelCalls") or 0)

                cache["input"] += inp
                cache["output"] += out
                cache["cached"] += cch
                cache["reasoning"] += rsn
                cache["total"] += tot
                cache["modelCalls"] += mc
                cache["turns"] += 1
                cache["lastTurn"] = {
                    "inputTokens": inp,
                    "outputTokens": out,
                    "cachedReadTokens": cch,
                    "reasoningTokens": rsn,
                    "totalTokens": tot,
                    "modelCalls": mc,
                    "apiDurationMs": int(u.get("apiDurationMs") or 0),
                    "numTurns": int(u.get("numTurns") or 0),
                }

                mu = u.get("modelUsage") or {}
                for name, m in mu.items():
                    if not isinstance(m, dict):
                        continue
                    if name not in cache["byModel"]:
                        cache["byModel"][name] = {
                            "inputTokens": 0,
                            "outputTokens": 0,
                            "cachedReadTokens": 0,
                            "reasoningTokens": 0,
                            "totalTokens": 0,
                            "modelCalls": 0,
                        }
                    bm = cache["byModel"][name]
                    bm["inputTokens"] += int(m.get("inputTokens") or 0)
                    bm["outputTokens"] += int(m.get("outputTokens") or 0)
                    bm["cachedReadTokens"] += int(m.get("cachedReadTokens") or 0)
                    bm["reasoningTokens"] += int(m.get("reasoningTokens") or 0)
                    bm["totalTokens"] += int(m.get("totalTokens") or 0)
                    bm["modelCalls"] += int(m.get("modelCalls") or 0)

            cache["pos"] = f.tell()
            cache["length"] = size
    except OSError as e:
        empty["parseError"] = str(e)
        return empty

    _usage_cache[key] = cache
    return {
        "inputTokens": cache["input"],
        "outputTokens": cache["output"],
        "cachedReadTokens": cache["cached"],
        "reasoningTokens": cache["reasoning"],
        "totalTokens": cache["total"],
        "modelCalls": cache["modelCalls"],
        "turnUsageCount": cache["turns"],
        "byModel": cache["byModel"],
        "lastTurn": cache["lastTurn"],
    }


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        if "." in s2:
            head, rest = s2.split(".", 1)
            frac = ""
            tz = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tz = rest[i:]
                    break
            frac = (frac + "000000")[:6]
            s2 = f"{head}.{frac}{tz}"
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def agent_snapshot(
    session_id: str,
    pid: int = 0,
    cwd: str = "",
    opened_at: str = "",
    listed_active: bool = False,
    provider: str = "grok",
) -> dict[str, Any]:
    """Build snapshot for one agent. Always extracts and exposes the *active model*."""
    directory = find_session_dir(session_id)
    summary = read_json(directory / "summary.json") if directory else None
    signals = read_json(directory / "signals.json") if directory else None
    usage = (
        get_usage_from_updates(directory / "updates.jsonl")
        if directory
        else {
            "inputTokens": 0,
            "outputTokens": 0,
            "cachedReadTokens": 0,
            "reasoningTokens": 0,
            "totalTokens": 0,
            "modelCalls": 0,
            "turnUsageCount": 0,
            "byModel": {},
            "lastTurn": None,
        }
    )

    alive = pid_alive(pid)
    title = None
    if summary:
        title = summary.get("generated_title") or summary.get("session_summary")
    if not title:
        title = f"Session {session_id[:8]}"

    # === ACTIVE MODEL TRACKING (critical for accurate pricing) ===
    model = "unknown"
    if summary and summary.get("current_model_id"):
        model = str(summary["current_model_id"])
    elif signals and signals.get("primaryModelId"):
        model = str(signals["primaryModelId"])
    # Fallback: most recent model from byModel usage if available
    if model == "unknown" and usage.get("byModel"):
        # pick the model with the highest totalTokens as the "active" one
        bym = usage["byModel"]
        if bym:
            model = max(bym.keys(), key=lambda k: int(bym[k].get("totalTokens") or 0))

    cfg = load_config()
    by_model = usage.get("byModel") or {}
    if by_model:
        cost = estimate_cost_usd(
            cfg,
            input_tokens=int(usage.get("inputTokens") or 0),
            cached_tokens=int(usage.get("cachedReadTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            model_id=model,
            by_model=by_model,
        )
    else:
        cost = estimate_cost_usd(
            cfg,
            input_tokens=int(usage.get("inputTokens") or 0),
            cached_tokens=int(usage.get("cachedReadTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            model_id=model if model != "unknown" else "grok-4.5",
        )
    usage = dict(usage)
    usage["costUsd"] = cost["costUsd"]
    usage["cost"] = cost

    agent_name = "grok"
    if summary and summary.get("agent_name"):
        agent_name = str(summary["agent_name"])

    ctx_used = int((signals or {}).get("contextTokensUsed") or 0)
    ctx_win = int((signals or {}).get("contextWindowTokens") or 0)
    ctx_pct = int((signals or {}).get("contextWindowUsage") or 0)
    if not ctx_pct and ctx_win > 0:
        ctx_pct = int(round(100.0 * ctx_used / ctx_win))

    tools = list((signals or {}).get("toolsUsed") or [])
    info_cwd = ""
    if summary and isinstance(summary.get("info"), dict):
        info_cwd = str(summary["info"].get("cwd") or "")

    return {
        "provider": provider,
        "sessionId": session_id,
        "shortId": session_id[:8],
        "title": title,
        "agentName": agent_name,
        "model": model,  # ACTIVE MODEL - used for pricing & UI badge
        "cwd": cwd or info_cwd,
        "pid": pid,
        "alive": alive,
        "listedActive": listed_active,
        "openedAt": opened_at,
        "createdAt": str((summary or {}).get("created_at") or ""),
        "updatedAt": str((summary or {}).get("updated_at") or ""),
        "lastActiveAt": str((summary or {}).get("last_active_at") or ""),
        "sessionDir": str(directory) if directory else None,
        "contextTokensUsed": ctx_used,
        "contextWindowTokens": ctx_win,
        "contextWindowUsage": ctx_pct,
        "turnCount": int((signals or {}).get("turnCount") or 0),
        "toolCallCount": int((signals or {}).get("toolCallCount") or 0),
        "toolsUsed": tools,
        "sessionDurationSeconds": int((signals or {}).get("sessionDurationSeconds") or 0),
        "avgTimeToFirstTokenMs": int((signals or {}).get("avgTimeToFirstTokenMs") or 0),
        "avgResponseTimeMs": int((signals or {}).get("avgResponseTimeMs") or 0),
        "compactionCount": int((signals or {}).get("compactionCount") or 0),
        "processRssBytes": process_rss(pid) if alive else 0,
        "usage": usage,
    }


def collect_grok_agents(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Full Grok collector - preserves original behavior + tags provider."""
    rebuild_session_index()
    active_raw = read_json(ACTIVE_PATH) or []
    if not isinstance(active_raw, list):
        active_raw = [active_raw]

    agents: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in active_raw:
        if not isinstance(a, dict):
            continue
        sid = str(a.get("session_id") or "").strip()
        if not sid or " " in sid or sid in seen:
            continue
        seen.add(sid)
        pid = a.get("pid") or 0
        try:
            pid = int(pid)
        except Exception:
            pid = 0
        agents.append(
            agent_snapshot(
                sid,
                pid=pid,
                cwd=str(a.get("cwd") or ""),
                opened_at=str(a.get("opened_at") or ""),
                listed_active=True,
                provider="grok",
            )
        )
    return agents


def collect_stub_provider(provider_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Stub collector for Cursor / Anthropic / OpenAI.

    Returns empty list for now. Extend by parsing provider_cfg['logsPath']
    and extracting sessions + active model from log lines / JSON.
    """
    # Future: implement log parsers that extract model names from request payloads
    # e.g. search for "model": "claude-3-5-sonnet..." or "gpt-4o"
    return []


def list_grok_processes() -> list[dict[str, Any]]:
    procs: list[dict[str, Any]] = []
    seen: set[int] = set()

    active = read_json(ACTIVE_PATH) or []
    if not isinstance(active, list):
        active = [active]
    for a in active:
        if not isinstance(a, dict):
            continue
        try:
            pid = int(a.get("pid") or 0)
        except Exception:
            pid = 0
        if pid <= 0 or pid in seen or not pid_alive(pid):
            continue
        seen.add(pid)
        procs.append(
            {
                "pid": pid,
                "name": "grok",
                "startTime": "",
                "workingSetBytes": process_rss(pid),
            }
        )

    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == INVALID_HANDLE_VALUE or snap is None or snap == -1:
            return procs
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not k32.Process32FirstW(snap, ctypes.byref(entry)):
                return procs
            wanted = {"grok.exe", "agent.exe"}
            while True:
                name = (entry.szExeFile or "").lower()
                pid = int(entry.th32ProcessID)
                if name in wanted and pid not in seen:
                    seen.add(pid)
                    procs.append(
                        {
                            "pid": pid,
                            "name": name.replace(".exe", ""),
                            "startTime": "",
                            "workingSetBytes": process_rss(pid),
                        }
                    )
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            k32.CloseHandle(snap)
    except Exception:
        pass
    return procs


def get_dashboard_state() -> dict[str, Any]:
    cfg = load_config()
    providers_cfg = cfg.get("providers") or [{"id": "grok", "type": "grok", "enabled": True}]

    all_agents: list[dict[str, Any]] = []
    provider_summaries: list[dict[str, Any]] = []

    for pcfg in providers_cfg:
        if not pcfg.get("enabled", True):
            continue
        ptype = (pcfg.get("type") or pcfg.get("id") or "").lower()
        pid = pcfg.get("id") or ptype

        if ptype == "grok":
            agents = collect_grok_agents(cfg)
        else:
            agents = collect_stub_provider(pcfg)

        # Tag and collect active models for this provider
        active_models: dict[str, int] = {}
        for ag in agents:
            ag["provider"] = pid
            m = ag.get("model") or "unknown"
            active_models[m] = active_models.get(m, 0) + 1

        provider_summaries.append({
            "id": pid,
            "name": pcfg.get("name") or pid,
            "type": ptype,
            "enabled": True,
            "agentCount": len(agents),
            "aliveCount": sum(1 for a in agents if a.get("alive")),
            "activeModels": active_models,  # model -> count of agents using it
            "notes": pcfg.get("notes") or "",
        })
        all_agents.extend(agents)

    # Recent Grok sessions (24h)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent: list[dict[str, Any]] = []
    seen_ids = {a["sessionId"] for a in all_agents}
    for sid, path in list(_session_index.items()):
        if sid in seen_ids:
            continue
        summary = read_json(Path(path) / "summary.json")
        if not summary:
            continue
        last = parse_dt(summary.get("last_active_at") or summary.get("updated_at"))
        if not last:
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last < cutoff:
            continue
        snap = agent_snapshot(sid, listed_active=False, provider="grok")
        if snap["usage"]["totalTokens"] == 0 and snap["turnCount"] == 0:
            continue
        recent.append(snap)

    def sort_key(x: dict[str, Any]) -> datetime:
        d = parse_dt(x.get("lastActiveAt") or x.get("updatedAt"))
        return d or datetime.min.replace(tzinfo=timezone.utc)

    recent.sort(key=sort_key, reverse=True)
    recent = recent[:15]

    # Totals across all providers
    totals = {
        "agentsAlive": sum(1 for a in all_agents if a["alive"]),
        "agentsListed": len(all_agents),
        "inputTokens": 0,
        "outputTokens": 0,
        "cachedReadTokens": 0,
        "reasoningTokens": 0,
        "totalTokens": 0,
        "modelCalls": 0,
        "contextTokensUsed": 0,
        "costUsd": 0.0,
        "byModel": {},
        "byProvider": {},
    }
    for ag in all_agents:
        u = ag["usage"]
        prov = ag.get("provider") or "unknown"
        totals["inputTokens"] += u["inputTokens"]
        totals["outputTokens"] += u["outputTokens"]
        totals["cachedReadTokens"] += u["cachedReadTokens"]
        totals["reasoningTokens"] += u["reasoningTokens"]
        totals["totalTokens"] += u["totalTokens"]
        totals["modelCalls"] += u["modelCalls"]
        totals["contextTokensUsed"] += ag["contextTokensUsed"]
        totals["costUsd"] += float(u.get("costUsd") or 0)

        if prov not in totals["byProvider"]:
            totals["byProvider"][prov] = {"totalTokens": 0, "costUsd": 0.0, "agents": 0}
        totals["byProvider"][prov]["totalTokens"] += u["totalTokens"]
        totals["byProvider"][prov]["costUsd"] += float(u.get("costUsd") or 0)
        totals["byProvider"][prov]["agents"] += 1

        for mk, bm in (u.get("byModel") or {}).items():
            if mk not in totals["byModel"]:
                totals["byModel"][mk] = {
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cachedReadTokens": 0,
                    "reasoningTokens": 0,
                    "totalTokens": 0,
                    "modelCalls": 0,
                    "costUsd": 0.0,
                }
            tm = totals["byModel"][mk]
            for k in ("inputTokens", "outputTokens", "cachedReadTokens", "reasoningTokens", "totalTokens", "modelCalls"):
                tm[k] += int(bm.get(k) or 0)
            c = (u.get("cost") or {}).get("byModel") or []
            for part in c:
                if part.get("model") == mk:
                    tm["costUsd"] += float(part.get("costUsd") or 0)
    totals["costUsd"] = round(totals["costUsd"], 6)

    # Period / budget (same as before)
    limits = cfg.get("limits") or {}
    period_start, period_name = period_window(str(limits.get("period") or "month"))
    budget = float(limits.get("budgetUsd") or 0)
    warn_pct = float(limits.get("softWarnPercent") or 80)

    period = {
        "name": period_name,
        "start": period_start.isoformat() if period_start else None,
        "inputTokens": 0,
        "outputTokens": 0,
        "cachedReadTokens": 0,
        "totalTokens": 0,
        "costUsd": 0.0,
        "sessionCount": 0,
    }
    if period_start is not None:
        for sid, path in list(_session_index.items()):
            summary = read_json(Path(path) / "summary.json")
            if not summary:
                continue
            last = parse_dt(
                summary.get("last_active_at")
                or summary.get("updated_at")
                or summary.get("created_at")
            )
            if not last:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last < period_start:
                continue
            snap = next((a for a in all_agents if a["sessionId"] == sid), None)
            if snap is None:
                snap = next((a for a in recent if a["sessionId"] == sid), None)
            if snap is None:
                snap = agent_snapshot(sid, listed_active=False, provider="grok")
            u = snap["usage"]
            if int(u.get("totalTokens") or 0) == 0 and int(snap.get("turnCount") or 0) == 0:
                continue
            period["sessionCount"] += 1
            period["inputTokens"] += int(u.get("inputTokens") or 0)
            period["outputTokens"] += int(u.get("outputTokens") or 0)
            period["cachedReadTokens"] += int(u.get("cachedReadTokens") or 0)
            period["totalTokens"] += int(u.get("totalTokens") or 0)
            period["costUsd"] += float(u.get("costUsd") or 0)
        period["costUsd"] = round(period["costUsd"], 6)

    budget_info = {
        "budgetUsd": budget,
        "spentUsd": period["costUsd"] if period_name != "none" else totals["costUsd"],
        "remainingUsd": None,
        "usedPercent": None,
        "period": period_name,
        "softWarnPercent": warn_pct,
        "status": "ok",
    }
    spent = float(budget_info["spentUsd"] or 0)
    if budget <= 0 or period_name == "none":
        budget_info["status"] = "unlimited"
    else:
        budget_info["remainingUsd"] = round(max(0.0, budget - spent), 6)
        budget_info["usedPercent"] = round(100.0 * spent / budget, 2)
        if spent >= budget:
            budget_info["status"] = "over"
        elif budget_info["usedPercent"] >= warn_pct:
            budget_info["status"] = "warn"

    plan_cfg = cfg.get("plan") or {}
    plan_info = {
        "name": plan_cfg.get("name") or "Unknown",
        "billing": plan_cfg.get("billing") or "unknown",
        "monthlyUsd": float(plan_cfg.get("monthlyUsd") or 0),
        "notes": list(plan_cfg.get("notes") or []),
    }

    rl_cfg = cfg.get("rateLimits") or {}
    rate_limit_info = {
        "enabled": bool(rl_cfg.get("enabled")),
        "windowHours": float(rl_cfg.get("windowHours") or 2),
        "maxPrompts": int(rl_cfg.get("maxPrompts") or 0),
        "promptsUsed": 0,
        "remaining": None,
        "usedPercent": None,
        "status": "disabled",
        "note": "Optional prompt-cap proxy using active session turn counts.",
    }
    if rate_limit_info["enabled"] and rate_limit_info["maxPrompts"] > 0:
        prompts_used = sum(int(ag.get("turnCount") or 0) for ag in all_agents)
        rate_limit_info["promptsUsed"] = prompts_used
        rate_limit_info["remaining"] = max(0, rate_limit_info["maxPrompts"] - prompts_used)
        rate_limit_info["usedPercent"] = round(
            100.0 * prompts_used / rate_limit_info["maxPrompts"], 2
        )
        if prompts_used >= rate_limit_info["maxPrompts"]:
            rate_limit_info["status"] = "over"
        elif rate_limit_info["usedPercent"] >= float(limits.get("softWarnPercent") or 80):
            rate_limit_info["status"] = "warn"
        else:
            rate_limit_info["status"] = "ok"

    default_rates = rates_for_model(cfg, "grok-4.5")
    is_sub = str(plan_info.get("billing") or "").lower() == "subscription"
    pricing_info = {
        "currency": (cfg.get("pricing") or {}).get("currency") or "USD",
        "source": (cfg.get("pricing") or {}).get("source") or "",
        "defaultModel": "grok-4.5",
        "rates": default_rates,
        "isEstimate": True,
        "isApiEquivalent": is_sub,
        "note": (
            "Subscription plan: flat fee, not per-token API billing. "
            "USD shown is API-equivalent retail value of local token usage. "
            f"Plan fee: ${plan_info.get('monthlyUsd') or '?'}/mo. Not an official invoice."
            if is_sub
            else "Estimated from local usage at published rates. Not an official invoice."
        ),
    }

    sample = {
        "t": int(time.time()),
        "totalTokens": totals["totalTokens"],
        "inputTokens": totals["inputTokens"],
        "outputTokens": totals["outputTokens"],
        "cachedReadTokens": totals["cachedReadTokens"],
        "contextTokensUsed": totals["contextTokensUsed"],
        "agentsAlive": totals["agentsAlive"],
        "costUsd": totals["costUsd"],
        "periodCostUsd": period["costUsd"],
    }
    if (
        not _history
        or _history[-1].get("totalTokens") != sample["totalTokens"]
        or _history[-1].get("agentsAlive") != sample["agentsAlive"]
        or _history[-1].get("costUsd") != sample["costUsd"]
        or (sample["t"] - _history[-1]["t"]) >= 15
    ):
        _history.append(sample)
        while len(_history) > _MAX_HISTORY:
            _history.pop(0)

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "serverStarted": _server_started.isoformat(),
        "grokHome": str(GROK_HOME),
        "providers": provider_summaries,
        "totals": totals,
        "period": period,
        "budget": budget_info,
        "plan": plan_info,
        "rateLimit": rate_limit_info,
        "pricing": pricing_info,
        "agents": all_agents,
        "recent": recent,
        "history": list(_history),
        "processes": list_grok_processes(),
        "notes": [
            "Multi-provider dashboard. Each agent reports its *active model* for accurate pricing.",
            "Grok fully supported. Cursor/Anthropic/OpenAI are stubs — enable in config and extend collectors.",
            "USD estimates use the active model rates from config.json pricing.models.",
            f"Budget: edit {CONFIG_PATH} limits.budgetUsd / limits.period.",
        ],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/api/state", "/api/tokens"):
                state = get_dashboard_state()
                body = json.dumps(state, separators=(",", ":")).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            if path == "/api/health":
                self._send(200, b'{"ok":true,"pid":%d}' % os.getpid(), "application/json; charset=utf-8")
                return

            rel = "dashboard.html" if path == "/" else path.lstrip("/").replace("..", "")
            file_path = (ROOT / rel).resolve()
            if not str(file_path).startswith(str(ROOT.resolve())):
                self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            if not file_path.is_file():
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            data = file_path.read_bytes()
            ctype = "application/octet-stream"
            ext = file_path.suffix.lower()
            if ext in (".html", ".htm"):
                ctype = "text/html; charset=utf-8"
            elif ext == ".js":
                ctype = "application/javascript; charset=utf-8"
            elif ext == ".css":
                ctype = "text/css; charset=utf-8"
            elif ext == ".json":
                ctype = "application/json; charset=utf-8"
            self._send(200, data, ctype)
        except Exception as e:
            log(f"request error: {e}\n{traceback.format_exc()}")
            body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            self._send(500, body, "application/json; charset=utf-8")


def already_running() -> bool:
    try:
        import urllib.request

        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/health",
            headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    if already_running():
        log(f"Already running on {HOST}:{PORT}")
        return 0

    try:
        PID_PATH.write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass

    HTTPServer.allow_reuse_address = True
    try:
        server = HTTPServer((HOST, PORT), Handler)
    except OSError as e:
        log(f"Bind failed on {HOST}:{PORT}: {e}")
        return 1

    log(f"Listening on http://{HOST}:{PORT}/ (pid={os.getpid()})")
    log(f"Grok home: {GROK_HOME}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        try:
            if PID_PATH.is_file() and PID_PATH.read_text().strip() == str(os.getpid()):
                PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        log("Stopped")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        try:
            CRASH_PATH.write_text(
                f"{datetime.now(timezone.utc).isoformat()}\n{tb}\n", encoding="utf-8"
            )
        except OSError:
            pass
        try:
            log("FATAL:\n" + tb)
        except Exception:
            pass
        raise

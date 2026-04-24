import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import grpc
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import bot_pb2  # noqa: E402
import bot_pb2_grpc  # noqa: E402


GRPC_ENDPOINT = "127.0.0.1:50051"
POLICY_HEALTH_URL = "http://127.0.0.1:50055/health"
POLICY_PROFILE_URL = "http://127.0.0.1:50055/profile"
ANALYTICS_DIR = ROOT / "data" / "analytics"
POLICY_LOG_DIR = ROOT / "data" / "policy_logs"
POLICY_CONFIG_PATH = ROOT / "python" / "bot_policy" / "config" / "policy_config.json"
DASHBOARD_PREFS_PATH = ROOT / "data" / "config" / "dashboard_preferences.json"
ASSUMED_STOP_LOSS_BPS = 80.0
ASSUMED_PROFIT_FLOOR_BPS = 50.0
DEFAULT_SYMBOL_MODE = "all_pairs"
SYMBOL_PRESETS = {
    "btc_only": ["BTCUSDT"],
    "all_pairs": ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "ADAUSDT", "SOLUSDT", "XRPUSDT"],
}
DEFAULT_RUN_CONFIG = {
    "decision_interval_ms": 5000,
    "max_pos_frac": 0.15,
    "policy_id": "regime_router",
    "exec_mode": "MAKER",
    "leverage_fixed": 1.0,
    "feature_profile": "v2",
}
WARMUP_WINDOWS_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}
DEFAULT_SIGNAL_CONFIG = {
    "threshold": 0.00025,
    "rv_scale": 0.20,
}
app = FastAPI(title="Bot Mk3 Paper Dashboard")


def channel():
    ch = grpc.insecure_channel(GRPC_ENDPOINT)
    grpc.channel_ready_future(ch).result(timeout=3)
    return ch


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value, low, high):
    return max(low, min(high, value))


def _load_signal_config():
    try:
        data = json.loads(POLICY_CONFIG_PATH.read_text(encoding="utf-8"))
        return {
            "threshold": _as_float(data.get("threshold"), DEFAULT_SIGNAL_CONFIG["threshold"]),
            "rv_scale": _as_float(data.get("rv_scale"), DEFAULT_SIGNAL_CONFIG["rv_scale"]),
        }
    except Exception:
        return dict(DEFAULT_SIGNAL_CONFIG)


SIGNAL_CONFIG = _load_signal_config()


def _load_dashboard_prefs():
    try:
        data = json.loads(DASHBOARD_PREFS_PATH.read_text(encoding="utf-8"))
        mode = str(data.get("symbol_mode", DEFAULT_SYMBOL_MODE))
        if mode not in SYMBOL_PRESETS:
            mode = DEFAULT_SYMBOL_MODE
        return {"symbol_mode": mode}
    except Exception:
        return {"symbol_mode": DEFAULT_SYMBOL_MODE}


def _save_dashboard_prefs(prefs):
    DASHBOARD_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")


DASHBOARD_PREFS = _load_dashboard_prefs()


def _normalize_fill(session_id, item):
    side = str(item.get("side", ""))
    if side not in {"Buy", "Sell"}:
        return None
    fill = {
        "session_id": session_id,
        "symbol": str(item.get("symbol", "")),
        "side": side,
        "qty": _as_float(item.get("qty")),
        "price": _as_float(item.get("price")),
        "fee": _as_float(item.get("fee", item.get("fees", 0.0))),
        "ts": _as_int(item.get("ts", item.get("timestamp", 0))),
        "order_type": str(item.get("order_type", "")),
    }
    if not fill["symbol"] or fill["qty"] <= 0 or fill["price"] <= 0 or fill["ts"] <= 0:
        return None
    return fill


def _load_disk_fills():
    fills = []
    if not ANALYTICS_DIR.exists():
        return fills
    for session_dir in sorted(ANALYTICS_DIR.glob("run_*")):
        path = session_dir / "trades.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            rows = data.get("trades") or data.get("executions") or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        for row in rows:
            if isinstance(row, dict):
                fill = _normalize_fill(session_dir.name, row)
                if fill:
                    fills.append(fill)
    return fills


def _load_current_fills(analytics, sessions):
    fills = []
    for sid in sessions[-5:]:
        try:
            metrics = analytics.GetSessionMetrics(bot_pb2.SessionRequest(session_id=sid), timeout=3)
            report = json.loads(metrics.json_report or "{}")
        except Exception:
            continue
        for row in report.get("executions", []):
            if isinstance(row, dict):
                fill = _normalize_fill(sid, row)
                if fill:
                    fills.append(fill)
    return fills


def _dedupe_fills(fills):
    seen = set()
    out = []
    for fill in fills:
        key = (
            fill["session_id"],
            fill["symbol"],
            fill["side"],
            round(fill["qty"], 12),
            round(fill["price"], 8),
            fill["ts"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(fill)
    return sorted(out, key=lambda f: (f["ts"], f["session_id"], f["symbol"]))


def _reconstruct_closed_trades(fills):
    lots_by_symbol = {}
    closed = []
    for fill in _dedupe_fills(fills):
        symbol = fill["symbol"]
        lots = lots_by_symbol.setdefault(symbol, [])
        qty_left = fill["qty"]
        fill_fee_left = fill["fee"]

        while qty_left > 1e-12 and lots and lots[0]["side"] != fill["side"]:
            lot = lots[0]
            close_qty = min(qty_left, lot["qty"])
            entry_fee = lot["fee"] * (close_qty / lot["qty"]) if lot["qty"] > 0 else 0.0
            exit_fee = fill_fee_left * (close_qty / qty_left) if qty_left > 0 else 0.0
            if lot["side"] == "Buy":
                pnl_gross = (fill["price"] - lot["price"]) * close_qty
                side = "Long"
            else:
                pnl_gross = (lot["price"] - fill["price"]) * close_qty
                side = "Short"
            fees = entry_fee + exit_fee
            closed.append(
                {
                    "session_id": fill["session_id"],
                    "symbol": symbol,
                    "side": side,
                    "qty": close_qty,
                    "entry_price": lot["price"],
                    "exit_price": fill["price"],
                    "fees": fees,
                    "pnl_net": pnl_gross - fees,
                    "exit_ts": fill["ts"],
                }
            )

            lot["qty"] -= close_qty
            lot["fee"] -= entry_fee
            qty_left -= close_qty
            fill_fee_left -= exit_fee
            if lot["qty"] <= 1e-12:
                lots.pop(0)

        if qty_left > 1e-12:
            lots.append(
                {
                    "side": fill["side"],
                    "qty": qty_left,
                    "price": fill["price"],
                    "fee": fill_fee_left,
                    "ts": fill["ts"],
                    "session_id": fill["session_id"],
                }
            )

    closed.sort(key=lambda t: t["exit_ts"])
    return closed


def _trade_stats(trades):
    wins = sum(1 for t in trades if _as_float(t.get("pnl_net")) > 0.0)
    losses = sum(1 for t in trades if _as_float(t.get("pnl_net")) < 0.0)
    closed = len(trades)
    pnl_net_total = sum(_as_float(t.get("pnl_net")) for t in trades)
    win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
    return {
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl_net_total": pnl_net_total,
    }


def _fmt_mmss(ms):
    total = max(0, int(ms // 1000))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _compute_warmup(start_time_ms):
    start_time_ms = _as_int(start_time_ms)
    if start_time_ms <= 0:
        return {
            "elapsed_ms": 0,
            "elapsed_label": "00:00",
            "horizons": {
                label: {
                    "ready": False,
                    "progress": 0.0,
                    "elapsed_ms": 0,
                    "remaining_ms": window_ms,
                    "label": f"00:00 / {_fmt_mmss(window_ms)}",
                }
                for label, window_ms in WARMUP_WINDOWS_MS.items()
            },
        }
    now_ms = int(time.time() * 1000)
    elapsed_ms = max(0, now_ms - start_time_ms)
    horizons = {}
    for label, window_ms in WARMUP_WINDOWS_MS.items():
        progress = 1.0 if window_ms <= 0 else _clamp(elapsed_ms / window_ms, 0.0, 1.0)
        horizons[label] = {
            "ready": progress >= 0.999,
            "progress": progress,
            "elapsed_ms": elapsed_ms,
            "remaining_ms": max(0, window_ms - elapsed_ms),
            "label": "Listo" if progress >= 0.999 else f"{_fmt_mmss(elapsed_ms)} / {_fmt_mmss(window_ms)}",
        }
    return {
        "elapsed_ms": elapsed_ms,
        "elapsed_label": _fmt_mmss(elapsed_ms),
        "horizons": horizons,
    }


def _latest_run_info():
    runs = sorted(ANALYTICS_DIR.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        return {"run_id": "", "start_time_ms": 0}
    run = runs[0]
    return {"run_id": run.name, "start_time_ms": int(run.stat().st_mtime * 1000)}


def _run_start_ms(start_time_ms, run_id):
    now_ms = int(time.time() * 1000)
    start_time_ms = _as_int(start_time_ms)
    if 0 < start_time_ms <= now_ms:
        return start_time_ms
    if run_id.startswith("run_"):
        try:
            dt = datetime.strptime(run_id[4:], "%Y%m%d_%H%M%S")
            parsed = int(dt.timestamp() * 1000)
            if 0 < parsed <= now_ms:
                return parsed
            return 0
        except Exception:
            return 0
    return 0


def _infer_symbol_mode(symbols):
    active = sorted(s for s in symbols if s)
    if active == ["BTCUSDT"]:
        return "btc_only"
    if active == sorted(SYMBOL_PRESETS["all_pairs"]):
        return "all_pairs"
    stored = DASHBOARD_PREFS.get("symbol_mode", DEFAULT_SYMBOL_MODE)
    return stored if stored in SYMBOL_PRESETS else DEFAULT_SYMBOL_MODE


def _build_symbol_configs(mode_name):
    symbols = SYMBOL_PRESETS.get(mode_name, SYMBOL_PRESETS[DEFAULT_SYMBOL_MODE])
    rows = []
    for symbol in symbols:
        rows.append(
            bot_pb2.SymbolConfig(
                symbol=symbol,
                decision_interval_ms=DEFAULT_RUN_CONFIG["decision_interval_ms"],
                max_pos_frac=DEFAULT_RUN_CONFIG["max_pos_frac"],
                policy_id=DEFAULT_RUN_CONFIG["policy_id"],
                exec_mode=DEFAULT_RUN_CONFIG["exec_mode"],
                leverage_mode=bot_pb2.LEVERAGE_MODE_FIXED,
                leverage_fixed=DEFAULT_RUN_CONFIG["leverage_fixed"],
                auto_min_leverage=DEFAULT_RUN_CONFIG["leverage_fixed"],
                auto_max_leverage=DEFAULT_RUN_CONFIG["leverage_fixed"],
                feature_profile=DEFAULT_RUN_CONFIG["feature_profile"],
            )
        )
    return rows


def _orchestrator_status(stub):
    return stub.GetOrchestratorStatus(bot_pb2.GetOrchestratorStatusRequest(), timeout=5)


def _restart_symbol_universe(mode_name):
    if mode_name not in SYMBOL_PRESETS:
        raise ValueError("Modo de símbolos inválido.")

    ch = channel()
    orch = bot_pb2_grpc.OrchestratorServiceStub(ch)
    status = _orchestrator_status(orch)
    current_mode = status.mode or "PAPER"
    if current_mode == "LIVE":
        live_positions = [s.symbol for s in status.symbols if str(s.position_side) != "Flat" or abs(float(s.position_qty)) > 1e-9]
        if live_positions:
            raise RuntimeError(f"Modo LIVE con posiciones abiertas: {', '.join(live_positions)}. Cerralas antes de cambiar el universo.")

    if status.state == "RUNNING":
        orch.StopOrchestrator(bot_pb2.StopOrchestratorRequest(), timeout=10)

    if current_mode == "PAPER":
        orch.ResetPaperState(bot_pb2.Empty(), timeout=10)

    resp = orch.StartOrchestrator(
        bot_pb2.StartOrchestratorRequest(
            mode=current_mode,
            symbols=_build_symbol_configs(mode_name),
            allow_live=(current_mode == "LIVE"),
            record_experience=False,
        ),
        timeout=20,
    )
    if resp.status != "STARTED":
        raise RuntimeError(resp.status or "No se pudo reiniciar el orquestador.")

    DASHBOARD_PREFS["symbol_mode"] = mode_name
    _save_dashboard_prefs(DASHBOARD_PREFS)
    return {"run_id": resp.run_id, "status": resp.status, "symbol_mode": mode_name}


def _position_risk(side, entry_price, notional_usdt, stop_loss_bps):
    if entry_price <= 0.0 or notional_usdt <= 0.0 or side not in {"Buy", "Sell"}:
        return 0.0, 0.0
    stop_move = stop_loss_bps / 10000.0
    if side == "Buy":
        stop_price = entry_price * (1.0 - stop_move)
    else:
        stop_price = entry_price * (1.0 + stop_move)
    risk_usdt = notional_usdt * stop_move
    return stop_price, risk_usdt


def _tail_jsonl(path, max_bytes=196_608):
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            blob = fh.read()
    except Exception:
        return []
    if not blob:
        return []
    lines = blob.decode("utf-8", errors="ignore").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _latest_policy_snapshots():
    if not POLICY_LOG_DIR.exists():
        return {}
    files = sorted(POLICY_LOG_DIR.glob("policy_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}
    latest = files[0]
    snapshots = {}
    for row in _tail_jsonl(latest):
        symbol = str(row.get("symbol", ""))
        ts = _as_int(row.get("ts"))
        if not symbol or ts <= 0:
            continue
        current = snapshots.get(symbol)
        if current is None or ts >= current["ts"]:
            snapshots[symbol] = {"ts": ts, "row": row}
    return {symbol: item["row"] for symbol, item in snapshots.items()}


def _latest_candidate_snapshots():
    if not ANALYTICS_DIR.exists():
        return {}
    runs = sorted(ANALYTICS_DIR.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        return {}
    latest = runs[0] / "candidates.jsonl"
    if not latest.exists():
        return {}
    snapshots = {}
    for row in _tail_jsonl(latest):
        symbol = str(row.get("symbol", ""))
        ts = _as_int((row.get("timestamps") or {}).get("decision_ts"))
        if not symbol or ts <= 0:
            continue
        current = snapshots.get(symbol)
        if current is None or ts >= current["ts"]:
            snapshots[symbol] = {"ts": ts, "row": row}
    return {symbol: item["row"] for symbol, item in snapshots.items()}


def _signal_meter(symbol, position_side, policy_row, candidate_row):
    if not policy_row:
        return {
            "score": 0.0,
            "bias": "neutral",
            "label": "Sin lectura",
            "detail": "Esperando policy",
            "action": "UNKNOWN",
            "reason": "waiting_policy",
            "edge_bps": 0.0,
            "ready": False,
        }

    ret_5s = _as_float(policy_row.get("ret_5s"))
    ret_10s = _as_float(policy_row.get("ret_10s"))
    ret_30s = _as_float(policy_row.get("ret_30s"))
    rv_30s = _as_float(policy_row.get("rv_30s"))
    obi_top1 = _as_float(policy_row.get("obi_top1"))
    action = str(policy_row.get("action", "HOLD"))
    reason = str(policy_row.get("reason", ""))

    base_thr = SIGNAL_CONFIG["threshold"]
    rv_scale = SIGNAL_CONFIG["rv_scale"]
    entry_thr = max(base_thr, rv_30s * rv_scale, 1e-9)

    directional_bias = (
        _clamp(ret_30s / (entry_thr * 1.30), -1.0, 1.0) * 0.50
        + _clamp(ret_10s / (entry_thr * 0.85), -1.0, 1.0) * 0.22
        + _clamp(ret_5s / (entry_thr * 0.60), -1.0, 1.0) * 0.16
        + _clamp(obi_top1, -1.0, 1.0) * 0.12
    )
    directional_bias = _clamp(directional_bias, -1.0, 1.0)

    edge_bps = _as_float((candidate_row or {}).get("expected_net_edge_bps"))
    intended_side = str((candidate_row or {}).get("side_intended", "None"))
    ready = intended_side not in {"", "None"}
    edge_readiness = _clamp((edge_bps - 0.20) / 1.80, 0.0, 1.0)
    magnitude_cap = 0.14 + (0.86 * max(1.0 if ready else 0.0, edge_readiness))
    score = _clamp(directional_bias * magnitude_cap, -1.0, 1.0)

    if action == "OPEN_LONG":
        score = max(score, 0.88)
        ready = True
    elif action == "OPEN_SHORT":
        score = min(score, -0.88)
        ready = True

    abs_score = abs(score)
    if abs_score < 0.08:
        bias = "neutral"
        label = "Neutral"
    elif abs_score < 0.22:
        bias = "long" if score > 0 else "short"
        label = "Vigilando long" if score > 0 else "Vigilando short"
    elif abs_score < 0.55:
        bias = "long" if score > 0 else "short"
        label = "Sesgo long" if score > 0 else "Sesgo short"
    else:
        bias = "long" if score > 0 else "short"
        label = "Listo long" if score > 0 else "Listo short"

    if ready:
        detail = f"Edge {edge_bps:+.2f} bps · {reason or action}"
    elif abs_score < 0.08:
        detail = f"Sin señal clara · edge {edge_bps:+.2f} bps"
    else:
        detail = f"Todavía no entra · edge {edge_bps:+.2f} bps"

    if position_side in {"Buy", "Sell"} and reason.startswith("exit_"):
        detail = f"Gestionando salida · {reason}"

    return {
        "score": score,
        "bias": bias,
        "label": label,
        "detail": detail,
        "action": action,
        "reason": reason,
        "edge_bps": edge_bps,
        "ready": ready,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(
        """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bot Mk3 Paper</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }
    body { margin: 0; background: #0b0f14; color: #e8eef7; }
    header { display:flex; align-items:center; justify-content:space-between; padding:18px 24px; border-bottom:1px solid #1e2937; background:#111821; position:sticky; top:0; }
    h1 { margin:0; font-size:18px; letter-spacing:0; }
    .badge { padding:7px 10px; border:1px solid #1f9d63; background:#0e2b20; color:#66f0a2; border-radius:6px; font-weight:700; }
    main { padding:24px; display:grid; gap:18px; }
    .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:14px; }
    .card { border:1px solid #1e2937; background:#121a24; border-radius:8px; padding:16px; }
    .label { color:#95a3b8; font-size:12px; text-transform:uppercase; }
    .value { font-size:24px; font-weight:750; margin-top:8px; }
    .price { font-size:38px; }
    .toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:6px; flex-wrap:wrap; }
    .segmented { display:inline-flex; border:1px solid #243244; border-radius:8px; overflow:hidden; background:#0f1720; }
    .segmented button { border:0; background:transparent; color:#95a3b8; padding:8px 12px; cursor:pointer; font:inherit; min-width:110px; }
    .segmented button.active { background:#1f2937; color:#e8eef7; }
    .segmented button:disabled { opacity:0.55; cursor:wait; }
    .quote-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-top:12px; }
    .quote { border:1px solid #1e2937; background:#0f1720; border-radius:6px; padding:12px; min-height:92px; }
    .quote .symbol { color:#95a3b8; font-size:12px; text-transform:uppercase; }
    .quote .mid { font-size:24px; font-weight:700; margin-top:6px; }
    .quote .meta { color:#95a3b8; font-size:12px; margin-top:6px; }
    .meter-wrap { margin-top:10px; }
    .meter-label { display:flex; justify-content:space-between; gap:8px; font-size:12px; color:#c9d4e4; margin-bottom:6px; }
    .meter-track { position:relative; height:12px; border-radius:999px; background:linear-gradient(90deg, #7f1d1d 0%, #b91c1c 22%, #eab308 50%, #22c55e 78%, #15803d 100%); overflow:hidden; border:1px solid #243244; }
    .meter-deadzone { position:absolute; left:42%; width:16%; top:0; bottom:0; background:rgba(234,179,8,0.30); }
    .meter-center { position:absolute; left:50%; top:-2px; bottom:-2px; width:2px; transform:translateX(-1px); background:#f8fafc; opacity:0.85; }
    .meter-needle { position:absolute; top:-4px; width:4px; height:20px; transform:translateX(-2px); border-radius:3px; background:#f8fafc; box-shadow:0 0 10px rgba(248,250,252,0.35); }
    .meter-sub { margin-top:6px; font-size:12px; color:#95a3b8; min-height:16px; }
    .warmup { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:6px; margin-top:8px; }
    .warmup-chip { border:1px solid #243244; border-radius:6px; padding:6px; background:#111821; }
    .warmup-chip.ready { border-color:#1f9d63; background:#0e2b20; }
    .warmup-chip .k { color:#95a3b8; font-size:11px; text-transform:uppercase; }
    .warmup-chip .v { color:#e8eef7; font-size:12px; margin-top:4px; }
    .sub { color:#95a3b8; font-size:13px; margin-top:6px; min-height:18px; }
    table { width:100%; border-collapse:collapse; font-size:14px; }
    th, td { padding:10px 12px; border-bottom:1px solid #1e2937; text-align:left; }
    th { color:#95a3b8; font-weight:600; }
    .pos { color:#6ff0a8; } .neg { color:#ff7b8a; } .muted { color:#95a3b8; }
    .status { display:flex; align-items:center; gap:8px; }
    .dot { width:8px; height:8px; border-radius:99px; background:#27df86; }
    .warn { background:#eab308; }
    .error { color:#ff7b8a; white-space:pre-wrap; }
  </style>
</head>
<body>
  <header>
    <h1>Bot Mk3 · Paper Trading</h1>
    <div class="badge" id="mode">PAPER</div>
  </header>
  <main>
    <div id="error" class="error"></div>
    <section class="grid">
      <div class="card"><div class="label">Estado</div><div class="value status"><span id="dot" class="dot"></span><span id="state">...</span></div></div>
      <div class="card"><div class="label">Equity Paper</div><div class="value" id="equity">...</div></div>
      <div class="card"><div class="label">Exposición total</div><div class="value" id="exposure">...</div><div class="sub" id="marginSub"></div></div>
      <div class="card"><div class="label">Riesgo total</div><div class="value" id="riskTotal">...</div><div class="sub" id="riskSub"></div></div>
      <div class="card"><div class="label">Win Rate</div><div class="value" id="winRate">...</div><div class="sub" id="winRateSub"></div></div>
      <div class="card"><div class="label">Backend</div><div class="value" id="backend">...</div><div class="sub" id="backendSub"></div></div>
      <div class="card"><div class="label">Policy</div><div class="value" id="policy">...</div><div class="sub" id="policySub"></div></div>
      <div class="card"><div class="label">Seguridad</div><div class="value" id="safety">...</div><div class="sub" id="safetySub">Live apply apagado · PAPER only</div></div>
      <div class="card"><div class="label">Actualizado</div><div class="value" id="updated">...</div><div class="sub" id="feedSub"></div></div>
    </section>
    <section class="card">
      <div class="toolbar">
        <h2>Precios en vivo</h2>
        <div class="segmented">
          <button id="modeBtc" onclick="setSymbolMode('btc_only')">Solo BTC</button>
          <button id="modeAll" onclick="setSymbolMode('all_pairs')">Todos</button>
        </div>
      </div>
      <div class="sub" id="symbolModeSub">Universo cargando...</div>
      <div class="quote-grid" id="quotes"><div class="muted">Esperando feed...</div></div>
    </section>
    <section class="card">
      <h2>Posiciones</h2>
      <table>
        <thead><tr><th>Símbolo</th><th>Estado</th><th>Lado</th><th>Qty</th><th>Mid</th><th>Margen USDT</th><th>Notional USDT</th><th>Apal.</th><th>PnL abierto</th><th>Obs</th><th>Feed age</th></tr></thead>
        <tbody id="positions"><tr><td colspan="11" class="muted">Esperando datos...</td></tr></tbody>
      </table>
    </section>
    <section class="card">
      <h2>Trades cerrados</h2>
      <table>
        <thead><tr><th>Cierre</th><th>Sesión</th><th>Símbolo</th><th>Lado</th><th>Qty</th><th>Entrada</th><th>Salida</th><th>Fees</th><th>PnL neto</th></tr></thead>
        <tbody id="trades"><tr><td colspan="9" class="muted">Sin trades cerrados todavía.</td></tr></tbody>
      </table>
    </section>
  </main>
<script>
const money = n => Number(n || 0).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
const price = (symbol, n) => {
  const digits = (symbol === 'ADAUSDT' || symbol === 'DOGEUSDT') ? 4 : 2;
  return Number(n || 0).toLocaleString(undefined, {minimumFractionDigits:digits, maximumFractionDigits:digits});
};
const num = n => Number(n || 0).toLocaleString(undefined, {maximumFractionDigits:6});
const closeTime = ms => Number(ms || 0) > 0 ? new Date(Number(ms)).toLocaleString() : '-';
function cls(n) { return Number(n || 0) >= 0 ? 'pos' : 'neg'; }
function warmupHtml(warmup) {
  const horizons = (warmup || {}).horizons || {};
  const rows = ['5m', '15m', '1h'].map(label => {
    const item = horizons[label] || {};
    return `<div class="warmup-chip ${item.ready ? 'ready' : ''}"><div class="k">${label}</div><div class="v">${item.label || '-'}</div></div>`;
  });
  return `<div class="warmup">${rows.join('')}</div>`;
}
function meterHtml(signal) {
  const score = Math.max(-1, Math.min(1, Number((signal || {}).score || 0)));
  const needleLeft = ((score + 1) / 2) * 100;
  return `<div class="meter-wrap">
    <div class="meter-label"><span>${(signal || {}).label || 'Neutral'}</span><span>${score > 0 ? 'Long' : score < 0 ? 'Short' : 'Centro'}</span></div>
    <div class="meter-track">
      <div class="meter-deadzone"></div>
      <div class="meter-center"></div>
      <div class="meter-needle" style="left:${needleLeft}%;"></div>
    </div>
    <div class="meter-sub">${(signal || {}).detail || 'Sin lectura'}</div>
  </div>`;
}
async function setSymbolMode(mode) {
  const buttons = [document.getElementById('modeBtc'), document.getElementById('modeAll')];
  buttons.forEach(btn => btn.disabled = true);
  document.getElementById('symbolModeSub').textContent = 'Reiniciando universo de símbolos...';
  try {
    const res = await fetch('/api/symbol-mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode})
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    await load();
  } catch (e) {
    document.getElementById('error').textContent = String(e);
  } finally {
    buttons.forEach(btn => btn.disabled = false);
  }
}
async function load() {
  try {
    const fetchJson = async url => {
      const res = await fetch(url);
      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch (_) {
        throw new Error(text || `HTTP ${res.status}`);
      }
      if (!res.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      return data;
    };
    const [status, trades, health] = await Promise.all([
      fetchJson('/api/status'),
      fetchJson('/api/trades'),
      fetchJson('/api/health')
    ]);
    const warnings = [];
    if (status.ok === false) warnings.push(status.error || 'Backend no disponible.');
    if (trades.ok === false) warnings.push(trades.error || 'Trades no disponibles.');
    if (health.ok === false) warnings.push(health.error || 'Health check no disponible.');
    document.getElementById('error').textContent = warnings.join('\\n');
    document.getElementById('mode').textContent = status.mode || 'UNKNOWN';
    document.getElementById('state').textContent = status.state || 'UNKNOWN';
    document.getElementById('dot').className = 'dot' + (status.state === 'RUNNING' ? '' : ' warn');
    const primary = (status.symbols || [])[0] || {};
    document.getElementById('equity').textContent = '$' + money(status.equity);
    document.getElementById('exposure').textContent = '$' + money(status.exposure);
    document.getElementById('marginSub').textContent = `Margen usado: $${money(status.margin_used)}`;
    document.getElementById('riskTotal').textContent = '$' + money(status.risk_total_usdt);
    document.getElementById('riskSub').textContent = `SL ${money(status.stop_loss_bps)} bps · ${Number(status.risk_total_pct_equity || 0).toLocaleString(undefined, {maximumFractionDigits:2})}% equity`;
    document.getElementById('winRate').textContent = `${Number((trades.stats || {}).win_rate || 0).toLocaleString(undefined, {maximumFractionDigits:1})}%`;
    document.getElementById('winRateSub').textContent = `${(trades.stats || {}).wins || 0} gan / ${(trades.stats || {}).losses || 0} perd · ${((trades.stats || {}).closed_trades || 0)} cerrados · PnL $${money((trades.stats || {}).pnl_net_total)}`;
    document.getElementById('backend').textContent = health.backend_ok ? 'OK' : 'FALLA';
    document.getElementById('backendSub').textContent = `gRPC ${health.grpc_endpoint || ''}`;
    document.getElementById('policy').textContent = health.policy_ok ? 'OK' : 'FALLA';
    document.getElementById('policySub').textContent = health.policy_ok ? `${health.policy.policy_type} · schema ${health.profile.schema_version} · obs ${health.profile.obs_dim}` : '';
    document.getElementById('safety').textContent = status.mode === 'PAPER' ? 'PAPER' : 'REVISAR';
    document.getElementById('safetySub').textContent = `Live apply apagado · PAPER only · floor ${money(status.profit_floor_bps)} bps · SL ${money(status.stop_loss_bps)} bps`;
    document.getElementById('updated').textContent = new Date().toLocaleTimeString();
    document.getElementById('feedSub').textContent = primary.symbol ? `Feed ${primary.status} · health ${primary.health_state || 'UNKNOWN'} · obs ${Math.round((primary.obs_quality || 0)*100)}%` : 'Sin símbolo activo';
    document.getElementById('modeBtc').classList.toggle('active', status.symbol_mode === 'btc_only');
    document.getElementById('modeAll').classList.toggle('active', status.symbol_mode !== 'btc_only');
    const warmup = status.warmup || {};
    document.getElementById('symbolModeSub').textContent = `${status.symbol_mode === 'btc_only' ? 'Solo BTC' : 'Todos los pares'} · warmup ${warmup.elapsed_label || '00:00'} · run ${status.run_id || '-'}`;
    const quotes = document.getElementById('quotes');
    quotes.innerHTML = '';
    if (!status.symbols.length) {
      quotes.innerHTML = '<div class="muted">Sin símbolos activos.</div>';
    } else {
      for (const s of status.symbols || []) {
        quotes.insertAdjacentHTML('beforeend', `<div class="quote"><div class="symbol">${s.symbol}</div><div class="mid">$${price(s.symbol, s.mid)}</div><div class="meta">${s.status} · ${s.health_state || 'UNKNOWN'} · ${Math.round((s.obs_quality || 0)*100)}% obs</div><div class="meta">Feed age ${Math.round(s.feed_age_ms || 0)}ms · OB ${s.ob_state || 'n/a'}</div>${meterHtml(s.signal_meter)}${warmupHtml(s.warmup)}</div>`);
      }
    }
    const pos = document.getElementById('positions');
    pos.innerHTML = '';
    if (!status.symbols.length) {
      pos.innerHTML = '<tr><td colspan="11" class="muted">Sin símbolos activos.</td></tr>';
    } else {
      for (const s of status.symbols || []) {
        pos.insertAdjacentHTML('beforeend', `<tr><td>${s.symbol}</td><td>${s.status} / ${s.health_state || 'UNKNOWN'}</td><td>${s.side}</td><td>${num(s.qty)}</td><td>${price(s.symbol, s.mid)}</td><td>$${money(s.margin_usdt)}</td><td>$${money(s.notional_usdt)}</td><td>${num(s.leverage)}x</td><td class="${cls(s.unrealized_pnl)}">${money(s.unrealized_pnl)}</td><td>${Math.round((s.obs_quality || 0)*100)}%</td><td>${Math.round(s.feed_age_ms || 0)}ms</td></tr>`);
      }
    }
    const tb = document.getElementById('trades');
    tb.innerHTML = '';
    if (!trades.trades.length) {
      tb.innerHTML = '<tr><td colspan="9" class="muted">Sin trades cerrados todavía.</td></tr>';
    } else {
      for (const t of trades.trades) {
        tb.insertAdjacentHTML('beforeend', `<tr><td>${closeTime(t.exit_ts)}</td><td>${t.session_id}</td><td>${t.symbol}</td><td>${t.side}</td><td>${num(t.qty)}</td><td>${price(t.symbol, t.entry_price)}</td><td>${price(t.symbol, t.exit_price)}</td><td>${money(t.fees)}</td><td class="${cls(t.pnl_net)}">${money(t.pnl_net)}</td></tr>`);
      }
    }
  } catch (e) {
    document.getElementById('error').textContent = String(e);
  }
}
load();
setInterval(load, 1000);
</script>
</body>
</html>
        """
    )


@app.get("/api/status")
def api_status():
    try:
        ch = channel()
        orch = bot_pb2_grpc.OrchestratorServiceStub(ch)
        st = orch.GetOrchestratorStatus(bot_pb2.GetOrchestratorStatusRequest(), timeout=3)
        policy_snapshots = _latest_policy_snapshots()
        candidate_snapshots = _latest_candidate_snapshots()
        run_info = _latest_run_info()
        run_id = run_info["run_id"]
        symbols = []
        risk_total_usdt = 0.0
        warmup_start = _run_start_ms(st.start_time, run_id) or run_info["start_time_ms"]
        warmup = _compute_warmup(warmup_start)
        for s in st.symbols:
            stop_price, risk_to_stop_usdt = _position_risk(
                s.position_side,
                s.entry_price,
                s.notional_value,
                ASSUMED_STOP_LOSS_BPS,
            )
            risk_total_usdt += risk_to_stop_usdt
            symbols.append(
                {
                    "symbol": s.symbol,
                    "status": s.status,
                    "side": s.position_side,
                    "qty": s.position_qty,
                    "entry_price": s.entry_price,
                    "mid": s.mid_price,
                    "margin_usdt": s.equity_alloc_used,
                    "notional_usdt": s.notional_value,
                    "unrealized_pnl": s.unrealized_pnl,
                    "realized_pnl": s.realized_pnl,
                    "leverage": s.effective_leverage,
                    "last_action": s.last_action,
                    "obs_quality": s.obs_quality,
                    "health_state": s.health_state,
                    "ob_state": s.ob_state,
                    "feed_age_ms": s.event_rate,
                    "stop_price": stop_price,
                    "risk_to_stop_usdt": risk_to_stop_usdt,
                    "warmup": warmup,
                    "signal_meter": _signal_meter(
                        s.symbol,
                        s.position_side,
                        policy_snapshots.get(s.symbol),
                        candidate_snapshots.get(s.symbol),
                    ),
                }
            )
        return {
            "ok": True,
            "state": st.state,
            "mode": st.mode,
            "run_id": run_id,
            "start_time": warmup_start,
            "equity": st.global_equity,
            "cash": st.global_cash,
            "exposure": st.global_exposure,
            "margin_used": st.global_margin_used,
            "risk_total_usdt": risk_total_usdt,
            "risk_total_pct_equity": (risk_total_usdt / st.global_equity * 100.0) if st.global_equity > 0 else 0.0,
            "stop_loss_bps": ASSUMED_STOP_LOSS_BPS,
            "profit_floor_bps": ASSUMED_PROFIT_FLOOR_BPS,
            "symbol_mode": _infer_symbol_mode([s.symbol for s in st.symbols]),
            "warmup": warmup,
            "symbols": symbols,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Backend no disponible o lento en {GRPC_ENDPOINT}: {exc}",
            "state": "UNAVAILABLE",
            "mode": "UNKNOWN",
            "equity": 0.0,
            "cash": 0.0,
            "exposure": 0.0,
            "run_id": "",
            "start_time": 0,
            "margin_used": 0.0,
            "risk_total_usdt": 0.0,
            "risk_total_pct_equity": 0.0,
            "stop_loss_bps": ASSUMED_STOP_LOSS_BPS,
            "profit_floor_bps": ASSUMED_PROFIT_FLOOR_BPS,
            "symbol_mode": DASHBOARD_PREFS.get("symbol_mode", DEFAULT_SYMBOL_MODE),
            "warmup": _compute_warmup(0),
            "symbols": [],
        }


@app.get("/api/health")
def api_health():
    backend_ok = False
    backend_error = ""
    policy = {}
    profile = {}
    policy_ok = False
    try:
        ch = channel()
        orch = bot_pb2_grpc.OrchestratorServiceStub(ch)
        orch.GetOrchestratorStatus(bot_pb2.GetOrchestratorStatusRequest(), timeout=3)
        backend_ok = True
    except Exception as exc:
        backend_error = str(exc)

    try:
        with urlopen(POLICY_HEALTH_URL, timeout=3) as resp:
            policy = json.loads(resp.read().decode("utf-8"))
        with urlopen(POLICY_PROFILE_URL, timeout=3) as resp:
            profile = json.loads(resp.read().decode("utf-8"))
        policy_ok = policy.get("status") == "OK"
    except Exception as exc:
        policy = {"error": str(exc)}

    return {
        "ok": backend_ok and policy_ok,
        "error": "" if backend_ok and policy_ok else backend_error or policy.get("error", ""),
        "backend_ok": backend_ok,
        "grpc_endpoint": GRPC_ENDPOINT,
        "policy_ok": policy_ok,
        "policy": policy,
        "profile": profile,
    }


@app.get("/api/trades")
def api_trades():
    out = []
    fills = _load_disk_fills()
    errors = []
    try:
        ch = channel()
        analytics = bot_pb2_grpc.AnalyticsServiceStub(ch)
        sessions = analytics.ListSessions(bot_pb2.Empty(), timeout=3).session_ids
        fills.extend(_load_current_fills(analytics, sessions))
    except Exception as exc:
        errors.append(str(exc))

    out = _reconstruct_closed_trades(fills)
    recent = list(reversed(out[-100:]))
    return {"ok": True, "error": "; ".join(errors), "trades": recent, "stats": _trade_stats(out)}


@app.post("/api/symbol-mode")
def api_symbol_mode(body: dict = Body(default={})):
    mode_name = str(body.get("mode", "")).strip()
    try:
        result = _restart_symbol_universe(mode_name)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "symbol_mode": DASHBOARD_PREFS.get("symbol_mode", DEFAULT_SYMBOL_MODE)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")

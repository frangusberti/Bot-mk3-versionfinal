import json
import sys
from pathlib import Path
from urllib.request import urlopen

import grpc
from fastapi import FastAPI
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
ASSUMED_STOP_LOSS_BPS = 80.0
ASSUMED_PROFIT_FLOOR_BPS = 50.0
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
    .quote-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-top:12px; }
    .quote { border:1px solid #1e2937; background:#0f1720; border-radius:6px; padding:12px; min-height:92px; }
    .quote .symbol { color:#95a3b8; font-size:12px; text-transform:uppercase; }
    .quote .mid { font-size:24px; font-weight:700; margin-top:6px; }
    .quote .meta { color:#95a3b8; font-size:12px; margin-top:6px; }
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
      <h2>Precios en vivo</h2>
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
const num = n => Number(n || 0).toLocaleString(undefined, {maximumFractionDigits:6});
const closeTime = ms => Number(ms || 0) > 0 ? new Date(Number(ms)).toLocaleString() : '-';
function cls(n) { return Number(n || 0) >= 0 ? 'pos' : 'neg'; }
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
    document.getElementById('feedSub').textContent = primary.status ? `Feed ${primary.status} · obs ${Math.round((primary.obs_quality || 0)*100)}%` : 'Sin símbolo activo';
    const quotes = document.getElementById('quotes');
    quotes.innerHTML = '';
    if (!status.symbols.length) {
      quotes.innerHTML = '<div class="muted">Sin símbolos activos.</div>';
    } else {
      for (const s of status.symbols || []) {
        quotes.insertAdjacentHTML('beforeend', `<div class="quote"><div class="symbol">${s.symbol}</div><div class="mid">$${money(s.mid)}</div><div class="meta">${s.status} · ${Math.round((s.obs_quality || 0)*100)}% obs</div><div class="meta">Feed age ${Math.round(s.feed_age_ms || 0)}ms</div></div>`);
      }
    }
    const pos = document.getElementById('positions');
    pos.innerHTML = '';
    if (!status.symbols.length) {
      pos.innerHTML = '<tr><td colspan="11" class="muted">Sin símbolos activos.</td></tr>';
    } else {
      for (const s of status.symbols || []) {
        pos.insertAdjacentHTML('beforeend', `<tr><td>${s.symbol}</td><td>${s.status}</td><td>${s.side}</td><td>${num(s.qty)}</td><td>${money(s.mid)}</td><td>$${money(s.margin_usdt)}</td><td>$${money(s.notional_usdt)}</td><td>${num(s.leverage)}x</td><td class="${cls(s.unrealized_pnl)}">${money(s.unrealized_pnl)}</td><td>${Math.round((s.obs_quality || 0)*100)}%</td><td>${Math.round(s.feed_age_ms || 0)}ms</td></tr>`);
      }
    }
    const tb = document.getElementById('trades');
    tb.innerHTML = '';
    if (!trades.trades.length) {
      tb.innerHTML = '<tr><td colspan="9" class="muted">Sin trades cerrados todavía.</td></tr>';
    } else {
      for (const t of trades.trades) {
        tb.insertAdjacentHTML('beforeend', `<tr><td>${closeTime(t.exit_ts)}</td><td>${t.session_id}</td><td>${t.symbol}</td><td>${t.side}</td><td>${num(t.qty)}</td><td>${money(t.entry_price)}</td><td>${money(t.exit_price)}</td><td>${money(t.fees)}</td><td class="${cls(t.pnl_net)}">${money(t.pnl_net)}</td></tr>`);
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
        symbols = []
        risk_total_usdt = 0.0
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
                    "ob_state": s.ob_state,
                    "feed_age_ms": s.event_rate,
                    "stop_price": stop_price,
                    "risk_to_stop_usdt": risk_to_stop_usdt,
                }
            )
        return {
            "ok": True,
            "state": st.state,
            "mode": st.mode,
            "equity": st.global_equity,
            "cash": st.global_cash,
            "exposure": st.global_exposure,
            "margin_used": st.global_margin_used,
            "risk_total_usdt": risk_total_usdt,
            "risk_total_pct_equity": (risk_total_usdt / st.global_equity * 100.0) if st.global_equity > 0 else 0.0,
            "stop_loss_bps": ASSUMED_STOP_LOSS_BPS,
            "profit_floor_bps": ASSUMED_PROFIT_FLOOR_BPS,
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
            "margin_used": 0.0,
            "risk_total_usdt": 0.0,
            "risk_total_pct_equity": 0.0,
            "stop_loss_bps": ASSUMED_STOP_LOSS_BPS,
            "profit_floor_bps": ASSUMED_PROFIT_FLOOR_BPS,
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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")

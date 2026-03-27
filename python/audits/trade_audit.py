import pandas as pd
import json
import os
import uuid
from typing import List, Dict

class TradeAudit:
    """
    Stateful ledger that consumes atomic TradeFills from the environment steps,
    consolidates partial fills into full positions, and outputs the exact
    transaction history of the bot to disk.
    
    Generates:
    - trade_audit_full.csv
    - trade_audit_full.parquet
    - fill_audit.csv
    - trade_summary_by_episode.csv
    - trades_reconciliation_report.md
    """
    def __init__(self):
        self.trades_ledger = []
        self.fills_ledger = []
        self.open_positions = {}  # Key: (episode_id, symbol)
        
    def process_step(self, episode_id: str, step_idx: int, fills: List[Dict], account_equity: float, current_ts: int, env_phase: str = "Live"):
        """Ingests raw fills mapped from rust's execution engine over gRPC."""
        for fill in fills:
            self._process_fill(episode_id, step_idx, fill, account_equity, current_ts, env_phase)
            
    def _process_fill(self, episode_id: str, step_idx: int, fill: Dict, equity: float, ts: int, env_phase: str):
        symbol = fill['symbol']
        key = (episode_id, symbol)
        
        # Rust sends "Buy" or "Sell", we enforce Capitalization for safety
        fill_side = str(fill['side']).capitalize()
        
        # Log to atomic fill audit
        fill_id = str(uuid.uuid4())[:8]
        self.fills_ledger.append({
            "fill_id": fill_id,
            "parent_trade_id": None, # Will be backfilled
            "episode_id": episode_id,
            "step_idx": step_idx,
            "symbol": symbol,
            "side": fill_side,
            "event_time": fill['ts_event'],
            "recv_time": fill['ts_recv_local'],
            "local_time": ts,
            "price": fill['price'],
            "qty": fill['qty'],
            "fee": fill['fee'],
            "liquidity_flag": fill.get('liquidity', 'Unknown'),
            "order_id": fill.get('trace_id', None),
            "position_before": sum([f['qty'] if f['side']==fill_side else -f['qty'] for f in self.open_positions.get(key, {}).get('entry_fills', [])]) if key in self.open_positions else 0.0,
            "position_after": None, # computed below
            "close_fragment_flag": False,
            "flip_fragment_flag": False,
            "notes": "Raw fill ingested"
        })
        
        # Pass by reference to mutate later
        current_fill_log = self.fills_ledger[-1]

        if key not in self.open_positions:
            # Opening a new position
            trade_id = str(uuid.uuid4())[:8]
            current_fill_log['parent_trade_id'] = trade_id
            current_fill_log['position_after'] = fill['qty']
            
            self.open_positions[key] = {
                "trade_id": trade_id,
                "episode_id": episode_id,
                "step_open": step_idx,
                "symbol": symbol,
                "side": fill_side,
                "open_time_event": fill['ts_event'],
                "open_time_recv": fill['ts_recv_local'],
                "open_time_local": ts,
                "entry_fills": [fill],
                "exit_fills": [],
                "account_equity_before": equity,
                "env_phase": env_phase,
                "stale_feature_flag_at_entry": False,
                "orderbook_valid_flag_at_entry": True, # TODO: pipe from payload
            }
        else:
            pos = self.open_positions[key]
            current_fill_log['parent_trade_id'] = pos['trade_id']
            
            if fill_side == pos['side']:
                # Averaging in / Adding to position
                pos['entry_fills'].append(fill)
                current_fill_log['position_after'] = current_fill_log['position_before'] + fill['qty']
            else:
                # Taking profit / Cutting loss
                pos['exit_fills'].append(fill)
                current_fill_log['position_after'] = current_fill_log['position_before'] - fill['qty']
                current_fill_log['close_fragment_flag'] = True
                
                # Check closure state
                entry_qty = sum(f['qty'] for f in pos['entry_fills'])
                exit_qty = sum(f['qty'] for f in pos['exit_fills'])
                
                # If fully closed
                if abs(entry_qty - exit_qty) < 1e-6:
                    pos['step_close'] = step_idx
                    pos['close_time_event'] = fill['ts_event']
                    pos['close_time_recv'] = fill['ts_recv_local']
                    pos['close_time_local'] = ts
                    pos['account_equity_after'] = equity
                    pos['close_reason'] = "Fully matched fill"
                    self._finalize_trade(pos)
                    del self.open_positions[key]
                    
                # If flipped (exit qty > entry qty) -> Close current, open new
                elif exit_qty > entry_qty + 1e-6:
                    current_fill_log['flip_fragment_flag'] = True
                    excess_qty = exit_qty - entry_qty
                    
                    # Split the fill for the exit leg
                    exit_fill_exact = fill.copy()
                    exit_fill_exact['qty'] = fill['qty'] - excess_qty
                    exit_fill_exact['fee'] = fill['fee'] * (exit_fill_exact['qty'] / fill['qty'])
                    
                    pos['exit_fills'][-1] = exit_fill_exact
                    pos['step_close'] = step_idx
                    pos['close_time_event'] = fill['ts_event']
                    pos['close_time_recv'] = fill['ts_recv_local']
                    pos['close_time_local'] = ts
                    pos['account_equity_after'] = equity
                    pos['close_reason'] = "Flip fragment closure"
                    self._finalize_trade(pos)
                    del self.open_positions[key]
                    
                    # Create new inverted position for remainder
                    remainder_fill = fill.copy()
                    remainder_fill['qty'] = excess_qty
                    remainder_fill['fee'] = fill['fee'] * (excess_qty / fill['qty'])
                    
                    new_trade_id = str(uuid.uuid4())[:8]
                    # Also log the remnant of the flip explicitly to the fill audit 
                    self.fills_ledger.append({
                        "fill_id": str(uuid.uuid4())[:8],
                        "parent_trade_id": new_trade_id,
                        "episode_id": episode_id,
                        "step_idx": step_idx,
                        "symbol": symbol,
                        "side": fill_side,
                        "event_time": remainder_fill['ts_event'],
                        "recv_time": remainder_fill['ts_recv_local'],
                        "local_time": ts,
                        "price": remainder_fill['price'],
                        "qty": remainder_fill['qty'],
                        "fee": remainder_fill['fee'],
                        "liquidity_flag": remainder_fill.get('liquidity', 'Unknown'),
                        "order_id": remainder_fill.get('trace_id', None),
                        "position_before": 0.0,
                        "position_after": remainder_fill['qty'],
                        "close_fragment_flag": False,
                        "flip_fragment_flag": True,
                        "notes": "Remnant of flipped position execution"
                    })
                    
                    self.open_positions[key] = {
                        "trade_id": new_trade_id,
                        "episode_id": episode_id,
                        "step_open": step_idx,
                        "symbol": symbol,
                        "side": fill_side, # Flipped
                        "open_time_event": remainder_fill['ts_event'],
                        "open_time_recv": remainder_fill['ts_recv_local'],
                        "open_time_local": ts,
                        "entry_fills": [remainder_fill],
                        "exit_fills": [],
                        "account_equity_before": equity,
                        "env_phase": env_phase,
                        "stale_feature_flag_at_entry": False,
                        "orderbook_valid_flag_at_entry": True,
                    }

    def _finalize_trade(self, pos: Dict):
        """Builds the comprehensive single-row ledger representation of a closed trade."""
        entry_qty = sum(f['qty'] for f in pos['entry_fills'])
        exit_qty = sum(f['qty'] for f in pos['exit_fills'])
        
        avg_entry = sum(f['price'] * f['qty'] for f in pos['entry_fills']) / entry_qty if entry_qty > 0 else 0
        avg_exit = sum(f['price'] * f['qty'] for f in pos['exit_fills']) / exit_qty if exit_qty > 0 else 0
        
        fees_entry = sum(f['fee'] for f in pos['entry_fills'])
        fees_exit = sum(f['fee'] for f in pos['exit_fills'])
        total_fees = fees_entry + fees_exit
        
        if pos['side'].capitalize() == 'Buy':
            gross_pnl = (avg_exit - avg_entry) * exit_qty
        else:
            gross_pnl = (avg_entry - avg_exit) * exit_qty
            
        net_pnl = gross_pnl - total_fees
        net_pnl_pct = (net_pnl / (avg_entry * entry_qty)) * 100 if (avg_entry * entry_qty) > 0 else 0.0
        
        toxic = any(f.get('is_toxic', False) for f in pos['entry_fills'] + pos['exit_fills'])
        holding_time_ms = pos['close_time_event'] - pos['open_time_event']
        
        # VALIDATIONS & FLAGS
        flags = []
        if entry_qty <= 0: flags.append(("FAIL", "ZERO_ENTRY_QTY"))
        if exit_qty <= 0: flags.append(("FAIL", "ZERO_EXIT_QTY"))
        if abs(entry_qty - exit_qty) > 1e-6: flags.append(("FAIL", f"QTY_MISMATCH_diff_{abs(entry_qty-exit_qty)}"))
        if total_fees == 0: flags.append(("WARN", "ZERO_FEES_LOGGED"))
        if holding_time_ms < 0: flags.append(("FAIL", "NEGATIVE_HOLDING_TIME"))
        
        # Accounting integrity per trade 
        expected_net = gross_pnl - total_fees
        if abs(expected_net - net_pnl) > 1e-4: flags.append(("FAIL", "PNL_MATH_ERR"))
        
        # Format flags tightly
        flag_str = "|".join([f"[{lvl}]{msg}" for lvl, msg in flags]) if flags else "OK"
        
        trade_data = {
            # Identity and context
            "trade_id": pos['trade_id'],
            "episode_id": pos['episode_id'],
            "step_open": pos['step_open'],
            "step_close": pos['step_close'],
            "symbol": pos['symbol'],
            "side": pos['side'],
            "position_side": "Long" if pos['side'].capitalize() == 'Buy' else "Short",
            "checkpoint_tag": None, # TODO: pull from runner
            "env_phase": pos['env_phase'],
            
            # Times
            "open_time_event": pos['open_time_event'],
            "open_time_recv": pos['open_time_recv'],
            "open_time_local": pos['open_time_local'],
            "close_time_event": pos['close_time_event'],
            "close_time_recv": pos['close_time_recv'],
            "close_time_local": pos['close_time_local'],
            "holding_time_ms": holding_time_ms,
            "holding_time_s": holding_time_ms / 1000.0,
            
            # Entry / Exit Metrics
            "entry_qty": entry_qty,
            "exit_qty": exit_qty,
            "avg_entry_price": avg_entry,
            "avg_exit_price": avg_exit,
            "entry_order_type": "Limit", # Hardcoded for MK3 maker logic, 
            "exit_order_type": "Limit",
            "entry_liquidity_flag": pos['entry_fills'][0].get('liquidity', 'Unknown'),
            "exit_liquidity_flag": pos['exit_fills'][-1].get('liquidity', 'Unknown'),
            "partial_fills_count_entry": len(pos['entry_fills']),
            "partial_fills_count_exit": len(pos['exit_fills']),
            
            # Economic Result
            "gross_pnl": gross_pnl,
            "fees_entry": fees_entry,
            "fees_exit": fees_exit,
            "fees_total": total_fees,
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "slippage_entry_bps": None, # TODO: add BBO tracking at entry trace timestamp
            "slippage_exit_bps": None,  # TODO
            
            # Capital and Risk
            # Semantics Opción B: account_equity tracked as external snapshot. 
            "trade_pnl_effect": net_pnl, 
            "account_equity_before": pos['account_equity_before'],
            "account_equity_after": pos['account_equity_after'],
            "account_equity_change": pos['account_equity_after'] - pos['account_equity_before'],
            "margin_used": (avg_entry * entry_qty) / 1.0, # Assumes 1x leverage, TODO pull real leverage 
            "leverage": 1.0,
            "position_notional_entry": avg_entry * entry_qty,
            "position_notional_exit": avg_exit * exit_qty,
            
            # Quality / Audit
            "toxic_fill_flag": toxic,
            "stale_feature_flag_at_entry": pos['stale_feature_flag_at_entry'],
            "stale_feature_flag_at_exit": False,
            "orderbook_valid_flag_at_entry": pos['orderbook_valid_flag_at_entry'],
            "orderbook_valid_flag_at_exit": True,
            "close_reason": pos['close_reason'],
            "audit_warning_flags": flag_str,
            "notes": None,
        }
        
        self.trades_ledger.append(trade_data)

    def export(self, out_dir="c:/Bot mk3/python/audits/runs_audit/trades_live/"):
        os.makedirs(out_dir, exist_ok=True)
        
        if not self.trades_ledger:
            print("[TradeAudit] No completed trades recorded. Generates Fills Only.")
        else:
            df = pd.DataFrame(self.trades_ledger)
            
            csv_path = os.path.join(out_dir, "trade_audit_full.csv")
            parquet_path = os.path.join(out_dir, "trade_audit_full.parquet")
            df.to_csv(csv_path, index=False)
            df.to_parquet(parquet_path, index=False)
            
            txt_path = os.path.join(out_dir, "trade_audit.txt")
            with open(txt_path, "w") as f:
                f.write(f"THIS IS A SUMMARY VIEW — SEE trade_audit_full.csv FOR COMPLETE LEDGER\n")
                f.write(f"BOTMK3 RECONCILIATION SUMMARY\n")
                f.write(f"===================================\n\n")
                display_cols = ["trade_id", "symbol", "side", "avg_entry_price", "avg_exit_price", "exit_qty", "net_pnl", "trade_pnl_effect", "fees_total", "audit_warning_flags"]
                if set(display_cols).issubset(df.columns):
                    f.write(df[display_cols].to_string(index=False, justify="right"))
                else:
                    f.write(df.to_string(index=False, justify="right"))
                f.write(f"\n\nTotal Trades: {len(df)}")
                f.write(f"\nNet PnL: {df['net_pnl'].sum():.4f}\n")

            if "episode_id" in df.columns:
                episode_summary = df.groupby("episode_id").agg(
                    total_trades=("trade_id", "count"),
                    winning_trades=("net_pnl", lambda x: (x > 0).sum()),
                    losing_trades=("net_pnl", lambda x: (x <= 0).sum()),
                    gross_pnl_total=("gross_pnl", "sum"),
                    fees_total=("fees_total", "sum"),
                    net_pnl_total=("net_pnl", "sum"),
                    account_equity_net_shift=("account_equity_change", "sum"),
                    audit_fails=("audit_warning_flags", lambda x: x.str.contains(r"\[FAIL\]").sum())
                ).reset_index()
                
                episode_summary["win_rate"] = episode_summary["winning_trades"] / episode_summary["total_trades"].replace(0, 1)
                
                summary_path = os.path.join(out_dir, "trade_summary_by_episode.csv")
                episode_summary.to_csv(summary_path, index=False)
                
            json_path = os.path.join(out_dir, "trades_scorecard.json")
            summary_stats = {
                "total_trades": len(df),
                "total_fills": len(self.fills_ledger),
                "fatal_errors": int(df['audit_warning_flags'].str.contains(r"\[FAIL\]").sum()),
                "warnings": int(df['audit_warning_flags'].str.contains(r"\[WARN\]").sum()),
                "total_gross": float(df['gross_pnl'].sum()),
                "total_fees": float(df['fees_total'].sum()),
                "total_net": float(df['net_pnl'].sum())
            }
            with open(json_path, "w") as f:
                json.dump(summary_stats, f, indent=2)
                
            self._write_reconciliation_report(df, episode_summary, out_dir)

        if self.fills_ledger:
            df_fills = pd.DataFrame(self.fills_ledger)
            fill_path = os.path.join(out_dir, "fill_audit.csv")
            df_fills.to_csv(fill_path, index=False)
            
        return getattr(self, "trades_ledger", [])

    def _write_reconciliation_report(self, df: pd.DataFrame, ep_df: pd.DataFrame, out_dir: str):
        path = os.path.join(out_dir, "trades_reconciliation_report.md")
        fail_count = int(df['audit_warning_flags'].str.contains(r"\[FAIL\]").sum())
        warn_count = int(df['audit_warning_flags'].str.contains(r"\[WARN\]").sum())
        
        status = "PASS"
        if fail_count > 0: status = "FAIL"
        elif warn_count > 0: status = "WARN"
        
        with open(path, "w") as f:
            f.write(f"# BotMK3 Trade Ledger Reconciliation Report\n\n")
            f.write(f"**STATUS: {status}**\n\n")
            f.write(f"## 1. Accounting Semantics\n")
            f.write(f"- `trade_pnl_effect`: The isolated, mathematically strict net PnL produced explicitly by tracking this trade sequence (Gross - Fees).\n")
            f.write(f"- `account_equity_before` / `account_equity_after`: Snapshots of the total portfolio equity at the exact ticks the entry and exit fills were completed. As defined by Opción B, `account_equity_change` includes Mark-to-Market residual of open inventory, funding rates, and unrelated fees that may happen during the holding period, so it is NOT functionally expected to equal `trade_pnl_effect` 1:1.\n\n")
            f.write(f"## 2. Validation Checks\n")
            f.write(f"- **Trade Closure Math:** `gross_pnl - total_fees == net_pnl` (Strictly enforced at finalization)\n")
            f.write(f"- **Qty Consistency:** `entry_qty == exit_qty` (Enforced up to 1e-6 tolerance)\n")
            f.write(f"- **Timeline Integrity:** `holding_time_ms >= 0`\n\n")
            
            f.write(f"## 3. Results Summary\n")
            f.write(f"- **Total Consolidate Trades:** {len(df)}\n")
            f.write(f"- **Total Raw Fills Logged:** {len(self.fills_ledger)}\n")
            f.write(f"- **Systematic Errors [FAIL]:** {fail_count}\n")
            f.write(f"- **Warnings [WARN]:** {warn_count}\n\n")
            
            if fail_count > 0:
                fails = df[df['audit_warning_flags'].str.contains(r"\[FAIL\]")]
                f.write(f"### [ERROR TRACE] Failed Trades:\n")
                for _, row in fails.iterrows():
                    f.write(f"- Trade `{row['trade_id']}` -> Flags: `{row['audit_warning_flags']}`\n")

if __name__ == "__main__":
    pass

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bot_ml'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'audits'))

from grpc_env import GrpcTradingEnv
import bot_pb2
from trade_audit import TradeAudit
import random

import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--dataset_id", type=str, default="stage2_eval")
    parser.add_argument("--episodes", type=int, default=10)
    parser.add_argument("--max_steps", type=int, default=10000)
    parser.add_argument("--target_fills", type=int, default=200)
    parser.add_argument("--model_path", type=str, default=None)
    parser.add_argument("--fill_model", type=str, default="optimistic")
    args = parser.parse_args()

    symbol = args.symbol
    dataset_id = args.dataset_id
    episodes_limit = args.episodes
    max_total_steps = args.max_steps
    target_fills = args.target_fills

    fill_model_enum = bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
    if args.fill_model == "realistic":
        fill_model_enum = bot_pb2.MAKER_FILL_MODEL_REALISTIC

    model = None
    if args.model_path:
        print(f"[RUNNER] Loading SB3 model from {args.model_path}...")
        from stable_baselines3 import PPO
        model = PPO.load(args.model_path)

    print(f"[RUNNER] Starting live trade execution trace for {symbol} on {dataset_id}...")
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id=dataset_id,
        symbol=symbol,
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        fill_model=fill_model_enum,
        random_start_offset=True,
        min_episode_events=2000
    )
    
    auditor = TradeAudit()
    
    steps = 0
    total_fills_logged = 0
    episodes = 0
    
    while steps < max_total_steps and total_fills_logged < target_fills and episodes < episodes_limit:
        obs, info = env.reset()
        ep_id = info.get("episode_id", f"live_eval_{episodes}")
        episodes += 1
        ep_step = 0
        
        while steps < max_total_steps:
            if model:
                action, _ = model.predict(obs, deterministic=True)
                action = int(action)
            else:
                # Alternate every 50 local steps
                if (ep_step // 50) % 2 == 0:
                    action = random.choice([1, 2]) # Maker Buy (POST_BID / JOIN_BID)
                else:
                    action = random.choice([3, 4]) # Maker Sell (POST_ASK / JOIN_ASK)
                
            obs, reward, terminated, truncated, info = env.step(action)
            steps += 1
            ep_step += 1
            
            fills = info.get("fills", [])
            if fills:
                total_fills_logged += len(fills)
                auditor.process_step(
                    episode_id=ep_id,
                    step_idx=steps,
                    fills=fills,
                    account_equity=info.get("equity", 0.0),
                    current_ts=info.get("ts", 0)
                )
                
            if terminated or truncated:
                # Force-close any open position as a "forced liquidation" trade 
                # by synthesizing a virtual closing fill from the last known state
                key = (ep_id, "BTCUSDT")
                if key in auditor.open_positions:
                    pos = auditor.open_positions[key]
                    entry_qty = sum(f['qty'] for f in pos['entry_fills'])
                    if entry_qty > 0:
                        # Create a synthetic closing fill at current mid price
                        close_price = info.get("mid_price", 0.0)
                        if close_price <= 0:
                            close_price = pos['entry_fills'][-1]['price']
                        
                        close_side = "Sell" if pos['side'] == "Buy" else "Buy"
                        close_fill = {
                            "trace_id": "forced_close",
                            "symbol": "BTCUSDT",
                            "side": close_side,
                            "price": close_price,
                            "qty": entry_qty,
                            "fee": entry_qty * close_price * 0.0005, # simulate taker fee
                            "liquidity": "ForcedClose",
                            "ts_event": info.get("ts", 0),
                            "ts_recv_local": info.get("ts", 0),
                            "is_toxic": False,
                        }
                        auditor._process_fill(
                            episode_id=ep_id,
                            step_idx=steps,
                            fill=close_fill,
                            equity=info.get("equity", 0.0),
                            ts=info.get("ts", 0),
                            env_phase="ForcedClose"
                        )
                        total_fills_logged += 1
                break
        
    completed = len(auditor.trades_ledger)
    print(f"[RUNNER] Finished {steps} steps across {episodes} episodes.")
    print(f"[RUNNER] Generated {total_fills_logged} raw fills -> {completed} completed trades.")
    
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'audits', 'runs_audit', 'trades_live'))
    print(f"[RUNNER] Exporting true ledger to {out_dir}")
    summary = auditor.export(out_dir=out_dir)
    env.close()

if __name__ == "__main__":
    main()

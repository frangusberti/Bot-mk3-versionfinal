import os, sys, json
from datetime import datetime
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

def run_validation(label, steps, rl_config):
    print(f"[{datetime.now()}] {label}: starting {steps} steps")
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50
    )
    obs, info = env.reset(options={"rl_config": rl_config})
    for step in range(steps):
        obs, reward, done, truncated, info = env.step(0)
        if step % 5000 == 0:
            print(f"  [{datetime.now()}] step {step}/{steps}")
        if done or truncated:
            obs, info = env.reset(options={"rl_config": rl_config})
    print(f"[{datetime.now()}] {label}: done")
    env.close()

if __name__ == "__main__":
    cfg = {
        "use_exit_curriculum_d1": True,
        "maker_first_exit_timeout_ms": 8000,
        "exit_maker_pricing_multiplier": 0.5,
        "profit_floor_bps": 0.0,
        "use_selective_entry": True,
    }
    run_validation("RAMA_B_DynFloor_25k", 25000, cfg)

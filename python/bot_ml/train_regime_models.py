"""
train_regime_models.py — Entrena un modelo PPO específico para un régimen de mercado.

Uso:
    python train_regime_models.py --regime TRENDING_UP  --steps 500000
    python train_regime_models.py --regime TRENDING_DOWN --steps 500000
    python train_regime_models.py --regime HIGH_VOL     --steps 300000
    python train_regime_models.py --regime SIDEWAYS     --steps 500000

El script:
  1. Carga todos los episodios del índice de datasets.
  2. Clasifica cada episodio por régimen dominante usando los features del parquet.
  3. Filtra solo los episodios del régimen objetivo.
  4. Entrena un PPO sobre esos episodios.
  5. Guarda en models/regime/{regime}/model.zip

Si hay pocos episodios del régimen objetivo (< --min_episodes),
entrena en todos los episodios como fallback.
"""
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import argparse
import time
import random
import numpy as np

REGIME_CHOICES = ["HIGH_VOL", "TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS"]

# Schema v7 obs indices (coinciden con FeatureRow::to_obs_vec)
IDX_RET_30S = 8
IDX_RV_30S  = 10


# ──────────────────────────────────────────────
# Régimen de un episodio a partir del parquet
# ──────────────────────────────────────────────

def _find_parquet(episode: dict) -> str | None:
    for key in ("parquet_path", "data_path"):
        if episode.get(key) and os.path.exists(episode[key]):
            return episode[key]
    dataset_id = episode.get("dataset_id", "")
    candidates = [
        f"runs/{dataset_id}/datasets/{dataset_id}/normalized_events.parquet",
        f"data/runs/{dataset_id}/datasets/{dataset_id}/normalized_events.parquet",
    ]
    return next((p for p in candidates if os.path.exists(p)), None)


def classify_episode(parquet_path: str, rv_high_vol: float, ret_trending: float) -> str | None:
    """Clasifica el régimen dominante de un episodio leyendo su parquet."""
    try:
        import pandas as pd
        df = pd.read_parquet(parquet_path)

        # Busca columnas de rv_30s y ret_30s por nombre o índice (obs_8, obs_10)
        rv_col = next(
            (c for c in df.columns if "rv_30s" in c.lower() or c == f"obs_{IDX_RV_30S}"), None
        )
        ret_col = next(
            (c for c in df.columns if "ret_30s" in c.lower() or c == f"obs_{IDX_RET_30S}"), None
        )
        if rv_col is None or ret_col is None:
            return None

        mean_rv  = float(df[rv_col].mean())
        mean_ret = float(df[ret_col].mean())

        if mean_rv > rv_high_vol:
            return "HIGH_VOL"
        if mean_ret > ret_trending:
            return "TRENDING_UP"
        if mean_ret < -ret_trending:
            return "TRENDING_DOWN"
        return "SIDEWAYS"
    except Exception:
        return None


def label_episodes(episodes: list, rv_high_vol: float, ret_trending: float) -> dict:
    """Clasifica todos los episodios y retorna un dict regime -> [episodes]."""
    buckets   = {r: [] for r in REGIME_CHOICES}
    unlabeled = 0

    for ep in episodes:
        parquet = _find_parquet(ep)
        if not parquet:
            unlabeled += 1
            continue
        regime = classify_episode(parquet, rv_high_vol, ret_trending)
        if regime:
            buckets[regime].append(ep)
        else:
            unlabeled += 1

    total = sum(len(v) for v in buckets.values())
    print(f"Clasificados {total} episodios, {unlabeled} sin etiquetar")
    for r, eps in buckets.items():
        print(f"  {r}: {len(eps)} episodios ({len(eps)/max(total,1)*100:.1f}%)")
    return buckets


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Entrena modelo PPO por régimen")
    parser.add_argument("--regime",       type=str,   required=True, choices=REGIME_CHOICES)
    parser.add_argument("--steps",        type=int,   default=500_000)
    parser.add_argument("--symbol",       type=str,   default="BTCUSDT")
    parser.add_argument("--index",        type=str,   default="data/index/datasets_index.json")
    parser.add_argument("--window",       type=int,   default=1800)
    parser.add_argument("--stride",       type=int,   default=300)
    parser.add_argument("--server",       type=str,   default="localhost:50051")
    parser.add_argument("--seed",         type=int,   default=42)
    parser.add_argument("--load_model",   type=str,   default="")
    parser.add_argument("--rv_high_vol",  type=float, default=0.003)
    parser.add_argument("--ret_trending", type=float, default=0.0003)
    parser.add_argument("--min_episodes", type=int,   default=10,
                        help="Mínimo de episodios del régimen. Usa todos si hay menos.")
    parser.add_argument("--leverage",     type=float, default=5.0)
    parser.add_argument("--pos_frac",     type=float, default=0.20)
    parser.add_argument("--ent_coef",     type=float, default=0.01)
    parser.add_argument("--learning_rate",type=float, default=1e-4)
    args = parser.parse_args()

    output_dir = f"models/regime/{args.regime.lower()}"
    log_dir    = f"python/runs_train/regime_{args.regime.lower()}_{int(time.time())}"
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(log_dir,    exist_ok=True)

    print(f"=== Régimen objetivo: {args.regime} ===")
    print(f"Modelo de salida: {output_dir}/model.zip")

    # 1. Construir episodios
    from episode_builder import EpisodeBuilder
    builder     = EpisodeBuilder(args.index)
    symbols     = args.symbol.split(",")
    all_episodes = builder.build_windows(
        symbols, window_len_secs=args.window, stride_secs=args.stride
    )
    if not all_episodes:
        print(f"Sin episodios en {args.index}. Saliendo.")
        return

    print(f"Total episodios disponibles: {len(all_episodes)}")

    # 2. Filtrar por régimen
    buckets  = label_episodes(all_episodes, args.rv_high_vol, args.ret_trending)
    episodes = buckets[args.regime]

    if len(episodes) < args.min_episodes:
        print(
            f"Solo {len(episodes)} episodios para {args.regime} "
            f"(mínimo {args.min_episodes}). Usando todos los episodios como fallback."
        )
        episodes = all_episodes

    random.seed(args.seed)
    random.shuffle(episodes)
    print(f"Entrenando con {len(episodes)} episodios")

    # 3. Entrenar
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor
    from stable_baselines3.common.callbacks import CheckpointCallback, CallbackList
    import window_env
    from offline_train import ProgressCallback, evaluate_model

    def make_env():
        return window_env.WindowTradingEnv(
            episodes=episodes,
            server_addr=args.server,
            seed=args.seed,
            max_leverage=args.leverage,
            max_pos_frac=args.pos_frac,
        )

    vec_env = DummyVecEnv([make_env])
    vec_env = VecMonitor(vec_env, log_dir)

    if args.load_model and os.path.exists(args.load_model):
        print(f"Fine-tuning desde {args.load_model}")
        model = PPO.load(args.load_model, env=vec_env)
        model.ent_coef     = args.ent_coef
        model.learning_rate = args.learning_rate
    else:
        model = PPO(
            "MlpPolicy", vec_env,
            verbose=0,
            ent_coef=args.ent_coef,
            learning_rate=args.learning_rate,
            seed=args.seed,
            batch_size=256,
            n_steps=2048,
            n_epochs=10,
            clip_range=0.2,
            target_kl=0.02,
        )

    callbacks = CallbackList([
        CheckpointCallback(
            save_freq=10_000,
            save_path=f"{log_dir}/checkpoints",
            name_prefix=f"ppo_{args.regime.lower()}",
        ),
        ProgressCallback(args.steps),
    ])

    try:
        model.learn(total_timesteps=args.steps, callback=callbacks)
        model.save(f"{output_dir}/model")
        print(f"✓ Guardado: {output_dir}/model.zip")

        # Walk-forward gate: evaluate on 3 temporal folds of the regime episodes
        from model_registry import ModelRegistry
        registry = ModelRegistry()

        n = len(episodes)
        fold_size = max(1, n // 3)
        folds = [
            episodes[:fold_size],
            episodes[fold_size: fold_size * 2],
            episodes[fold_size * 2:],
        ]
        folds = [f for f in folds if f]

        window_metrics_list = []
        for i, fold_eps in enumerate(folds):
            eval_eps = fold_eps[-10:] if len(fold_eps) > 10 else fold_eps
            def _make(eps=eval_eps):
                return window_env.WindowTradingEnv(
                    episodes=eps, server_addr=args.server, seed=args.seed,
                    max_leverage=args.leverage, max_pos_frac=args.pos_frac,
                )
            fold_env = DummyVecEnv([_make])
            wm = evaluate_model(model, fold_env, num_episodes=len(eval_eps))
            print(f"Walk-forward {i+1}/{len(folds)}: net_pnl={wm['net_pnl']:.4f}  max_dd={wm['max_dd']:.4f}  trades={wm['trade_count']:.0f}")
            window_metrics_list.append(wm)
            fold_env.close()

        model_id = registry.register_model(
            model_path=f"{output_dir}/model.zip",
            metrics=window_metrics_list[-1],
            parent_model_id=args.load_model or None,
            train_window={"regime": args.regime, "folds": len(folds), "episodes_total": n},
            feature_profile="Rich",
        )
        accepted = registry.judge_walkforward(
            model_id=model_id,
            window_metrics=window_metrics_list,
            min_passing_windows=2,
        )
        if accepted:
            print(f"Modelo {args.regime} ACEPTADO. Registrado como {model_id}.")
        else:
            print(f"Modelo {args.regime} RECHAZADO por walk-forward gate.")

    except KeyboardInterrupt:
        print("Interrumpido. Guardando...")
        model.save(f"{output_dir}/model_interrupted")
    except Exception as e:
        import traceback
        print(f"Error en entrenamiento: {e}")
        traceback.print_exc()
    finally:
        vec_env.close()


if __name__ == "__main__":
    main()

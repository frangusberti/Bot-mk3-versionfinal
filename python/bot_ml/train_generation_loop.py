"""
train_generation_loop.py - Run repeated offline training cycles as "generations".

This wrapper translates the repo's existing offline trainer into a generation loop:

- each generation runs one offline_train cycle;
- each cycle can bootstrap from the best accepted/live model available;
- each result is logged to JSONL and Markdown audit files;
- BTC is the default training symbol while the new multi-timeframe stack settles in.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BOT_ML_DIR = Path(__file__).resolve().parent
OFFLINE_TRAIN = BOT_ML_DIR / "offline_train.py"
DEFAULT_BOOTSTRAP = ROOT / "models" / "vnext_bc_fix.zip"
LIVE_POINTER = ROOT / "models" / "live" / "live_model.json"
REGISTRY_DIR = ROOT / "models" / "registry"
SCAN_FALLBACK_INDEX = ROOT / "index" / "__scan_fallback__.json"

if str(BOT_ML_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_ML_DIR))

from episode_builder import EpisodeBuilder  # noqa: E402

try:
    from stable_baselines3.common.save_util import load_from_zip_file
except Exception:  # pragma: no cover - training host dependency
    load_from_zip_file = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch repeated BTC-first training generations")
    parser.add_argument("--generations", type=int, default=500, help="How many generations to run")
    parser.add_argument("--steps-per-gen", type=int, default=25000, help="Timesteps per generation")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Training symbol")
    parser.add_argument("--index", type=str, default=str(ROOT / "index" / "datasets_index.json"), help="Dataset index path")
    parser.add_argument("--window", type=int, default=1800, help="Episode window in seconds")
    parser.add_argument("--stride", type=int, default=300, help="Episode stride in seconds")
    parser.add_argument("--server", type=str, default="localhost:50051", help="gRPC server")
    parser.add_argument("--threads", type=int, default=4, help="CPU threads per generation")
    parser.add_argument("--profile", type=str, default="Rich", help="Feature profile")
    parser.add_argument("--leverage", type=float, default=3.0, help="Training leverage cap")
    parser.add_argument("--pos-frac", type=float, default=0.15, help="Training max position fraction")
    parser.add_argument("--disaster-dd", type=float, default=0.12, help="Hard disaster drawdown")
    parser.add_argument("--learning-rate", type=float, default=1e-4, help="Learning rate")
    parser.add_argument("--ent-coef", type=float, default=0.01, help="Entropy coefficient")
    parser.add_argument("--target-kl", type=float, default=0.02, help="Target KL")
    parser.add_argument("--seed", type=int, default=42, help="Base RNG seed")
    parser.add_argument("--base-model", type=str, default="", help="Explicit bootstrap model path")
    parser.add_argument("--run-name", type=str, default="", help="Optional run root name")
    parser.add_argument("--pause-secs", type=float, default=1.0, help="Pause between generations")
    parser.add_argument("--start-gen", type=int, default=1, help="Starting generation number")
    parser.add_argument("--obs-dim", type=int, default=200, help="Expected observation dimension")
    parser.add_argument("--low-priority", action="store_true", help="Run child trainers with below-normal priority")
    return parser.parse_args()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_files() -> list[Path]:
    if not REGISTRY_DIR.exists():
        return []
    return sorted(REGISTRY_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def resolve_model_zip(path_str: str) -> str:
    if not path_str:
        return ""
    path = Path(path_str)
    candidates: list[Path] = []
    if path.is_file():
        candidates.append(path)
    if path.with_suffix(".zip").is_file():
        candidates.append(path.with_suffix(".zip"))
    if path.is_dir():
        candidates.extend(sorted(path.glob("*.zip")))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def latest_live_model() -> str:
    pointer = load_json(LIVE_POINTER)
    if not pointer:
        return ""
    return resolve_model_zip(pointer.get("path", ""))


def latest_accepted_model() -> str:
    accepted: list[tuple[float, str]] = []
    for entry_path in registry_files():
        payload = load_json(entry_path)
        if not payload:
            continue
        if payload.get("status") not in {"ACCEPTED", "LIVE"} and not payload.get("accepted"):
            continue
        zip_path = resolve_model_zip(payload.get("model_path", ""))
        if zip_path:
            accepted.append((entry_path.stat().st_mtime, zip_path))
    if not accepted:
        return ""
    accepted.sort(key=lambda item: item[0], reverse=True)
    return accepted[0][1]


def latest_bootstrap_model(explicit: str) -> str:
    for candidate in [
        resolve_model_zip(explicit),
        latest_live_model(),
        latest_accepted_model(),
        str(DEFAULT_BOOTSTRAP) if DEFAULT_BOOTSTRAP.is_file() else "",
    ]:
        if candidate:
            return candidate
    return ""


def model_obs_dim(model_path: str) -> int | None:
    if not model_path or not load_from_zip_file:
        return None
    try:
        data, _, _ = load_from_zip_file(model_path)
        space = data.get("observation_space")
        shape = getattr(space, "shape", None)
        if shape and len(shape) >= 1:
            return int(shape[0])
    except Exception:
        return None
    return None


def compatible_bootstrap(model_path: str, expected_obs_dim: int) -> str:
    if not model_path:
        return ""
    observed = model_obs_dim(model_path)
    if observed is None:
        return model_path
    if observed != expected_obs_dim:
        print(
            f"Bootstrap descartado por incompatibilidad de obs_dim: "
            f"{model_path} ({observed} != {expected_obs_dim})",
            flush=True,
        )
        return ""
    return model_path


def resolve_index(index_path: str, symbol: str, window: int, stride: int) -> tuple[str, int, int, str]:
    attempts = [
        (index_path, "index"),
        (str(SCAN_FALLBACK_INDEX), "filesystem-scan"),
    ]
    for candidate, mode in attempts:
        builder = EpisodeBuilder(candidate)
        episodes = builder.build_windows([symbol], window_len_secs=window, stride_secs=stride)
        if episodes:
            return candidate, len(episodes), len(builder.datasets), mode
    return index_path, 0, 0, "none"


def newest_registry_after(before: set[Path]) -> Path | None:
    after = registry_files()
    new_files = [path for path in after if path not in before]
    if new_files:
        return new_files[-1]
    if after:
        return after[-1]
    return None


def init_markdown(path: Path, args: argparse.Namespace, effective_index: str, source_mode: str, episodes: int, bootstrap: str) -> None:
    lines = [
        "# Auditoria de generaciones v8",
        "",
        f"- inicio: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- simbolo: `{args.symbol}`",
        f"- generaciones objetivo: `{args.generations}`",
        f"- steps por generacion: `{args.steps_per_gen}`",
        f"- dataset source: `{source_mode}`",
        f"- index efectivo: `{effective_index}`",
        f"- episodios detectados: `{episodes}`",
        f"- modelo base inicial: `{bootstrap or 'ninguno'}`",
        "",
        "| Gen | Estado | Modelo | Walk-forward | PnL | MaxDD | Trades | Motivo |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_markdown_row(path: Path, record: dict[str, Any]) -> None:
    line = (
        f"| {record['generation']} | {record['status']} | {record['model_id']} | "
        f"{record['walkforward']} | {record['net_pnl']} | {record['max_dd']} | "
        f"{record['trade_count']} | {record['reason']} |"
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fmt_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def summarize_registry(path: Path | None, generation: int, run_name: str, log_path: Path, return_code: int) -> dict[str, Any]:
    payload = load_json(path) if path else None
    metrics = payload.get("metrics_new") or payload.get("metrics") or {} if payload else {}
    walk = payload.get("walkforward", {}) if payload else {}
    record = {
        "generation": generation,
        "run_name": run_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "return_code": return_code,
        "registry_file": str(path) if path else "",
        "model_id": payload.get("model_id", "-") if payload else "-",
        "status": payload.get("status", "FAILED") if payload else "FAILED",
        "accepted": bool(payload.get("accepted", False)) if payload else False,
        "reason": (payload.get("reason") or payload.get("rejection_reason") or "").replace("|", "/") if payload else "training_failed",
        "walkforward": (
            f"{walk.get('passing', 0)}/{walk.get('required', 0)}"
            if walk else "-"
        ),
        "net_pnl": fmt_float(metrics.get("net_pnl")),
        "max_dd": fmt_float(metrics.get("max_dd")),
        "trade_count": fmt_float(metrics.get("trade_count"), digits=0),
        "log_path": str(log_path),
    }
    return record


def run_generation(
    args: argparse.Namespace,
    generation: int,
    run_root: Path,
    effective_index: str,
    bootstrap_model: str,
) -> tuple[dict[str, Any], str]:
    logs_dir = ensure_dir(run_root / "logs")
    gen_run_name = f"{run_root.name}_g{generation:04d}"
    log_path = logs_dir / f"gen_{generation:04d}.log"
    before_registry = set(registry_files())

    cmd = [
        sys.executable,
        "-u",
        str(OFFLINE_TRAIN),
        "--steps", str(args.steps_per_gen),
        "--symbol", args.symbol,
        "--index", effective_index,
        "--window", str(args.window),
        "--stride", str(args.stride),
        "--run_name", gen_run_name,
        "--server", args.server,
        "--seed", str(args.seed + generation - 1),
        "--ent_coef", str(args.ent_coef),
        "--learning_rate", str(args.learning_rate),
        "--target_kl", str(args.target_kl),
        "--threads", str(args.threads),
        "--leverage", str(args.leverage),
        "--pos_frac", str(args.pos_frac),
        "--disaster_dd", str(args.disaster_dd),
        "--profile", args.profile,
    ]
    if args.low_priority:
        cmd.append("--low-priority")
    if bootstrap_model:
        cmd.extend(["--load_model", bootstrap_model])

    print(f"[gen {generation:04d}] launch: {gen_run_name}", flush=True)
    with log_path.open("w", encoding="utf-8") as log_fh:
        log_fh.write(f"launch_time={datetime.now().isoformat(timespec='seconds')}\n")
        log_fh.write(f"bootstrap_model={bootstrap_model or 'none'}\n")
        log_fh.write("cmd=" + " ".join(cmd) + "\n\n")
        log_fh.flush()
        process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log_fh.write(line)
            log_fh.flush()
        return_code = process.wait()

    newest = newest_registry_after(before_registry)
    record = summarize_registry(newest, generation, gen_run_name, log_path, return_code)
    next_bootstrap = bootstrap_model
    if record["accepted"]:
        candidate = compatible_bootstrap(
            latest_live_model() or latest_accepted_model(),
            args.obs_dim,
        )
        if candidate:
            next_bootstrap = candidate
    return record, next_bootstrap


def main() -> None:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"gen_v8_btc_{stamp}"
    run_root = ensure_dir(ROOT / "python" / "runs_train" / run_name)

    effective_index, episodes, dataset_count, source_mode = resolve_index(
        args.index,
        args.symbol,
        args.window,
        args.stride,
    )
    if episodes == 0:
        raise SystemExit(
            f"No se encontraron episodios para {args.symbol}. "
            f"Revise datasets o index. index probado: {args.index}"
        )

    bootstrap = compatible_bootstrap(
        latest_bootstrap_model(args.base_model),
        args.obs_dim,
    )
    audit_jsonl = run_root / "generation_audit.jsonl"
    audit_md = run_root / "generation_audit.md"
    init_markdown(audit_md, args, effective_index, source_mode, episodes, bootstrap)

    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": args.symbol,
        "generations": args.generations,
        "steps_per_generation": args.steps_per_gen,
        "index_requested": args.index,
        "index_effective": effective_index,
        "dataset_source": source_mode,
        "datasets_detected": dataset_count,
        "episodes_detected": episodes,
        "initial_bootstrap_model": bootstrap,
        "cwd": str(ROOT),
    }
    (run_root / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    current_bootstrap = bootstrap
    total_generations = range(args.start_gen, args.start_gen + args.generations)
    for generation in total_generations:
        record, current_bootstrap = run_generation(
            args=args,
            generation=generation,
            run_root=run_root,
            effective_index=effective_index,
            bootstrap_model=current_bootstrap,
        )
        with audit_jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        append_markdown_row(audit_md, record)
        print(
            f"[gen {generation:04d}] status={record['status']} "
            f"accepted={record['accepted']} pnl={record['net_pnl']} "
            f"dd={record['max_dd']} wf={record['walkforward']}",
            flush=True,
        )
        time.sleep(max(0.0, args.pause_secs))

    print(f"Loop completado. Auditoria: {audit_md}", flush=True)


if __name__ == "__main__":
    main()

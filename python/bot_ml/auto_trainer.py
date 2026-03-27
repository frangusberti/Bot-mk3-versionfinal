import os
import time
import json
import logging
import subprocess
import datetime
import toml
import grpc
from typing import List, Dict, Optional

# Add parent dir to path for imports
import sys
sys.path.append(os.path.dirname(__file__))

import bot_pb2
import bot_pb2_grpc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("AutoTrainer")

class AutoTrainer:
    def __init__(self, config_path: str = "server_config.toml"):
        self.config_path = config_path
        self.config = {}
        self.last_run_timestamp = 0
        self.deployed_today = 0
        self.last_day = datetime.date.today()
        self.load_config()

    def load_config(self):
        try:
            full_config = toml.load(self.config_path)
            self.config = full_config.get("auto_train", {})
            self.paths = full_config.get("paths", {
                "experience_root": "runs",
                "models_root": "models",
                "registry_root": "models/registry"
            })
            logger.info(f"Config loaded: {self.config}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            # Use defaults if file missing or broken
            self.config = {
                "enabled": False,
                "interval_minutes": 60,
                "min_new_files": 5,
                "train_window_hours": 24,
                "max_steps_per_cycle": 100000,
                "dry_run": True,
                "symbol": "BTCUSDT",
                "max_models_per_day": 3
            }

    def get_new_experience_dirs(self) -> List[str]:
        """
        Scans explorer_root for subdirectories containing parquet files modified in the last train_window_hours.
        """
        exp_root = self.paths.get("experience_root", "runs")
        window_hours = self.config.get("train_window_hours", 24)
        cutoff = time.time() - (window_hours * 3600)
        
        relevant_dirs = []
        if not os.path.exists(exp_root):
             logger.warning(f"Experience root {exp_root} does not exist.")
             return []

        for run_id in os.listdir(exp_root):
            exp_dir = os.path.join(exp_root, run_id, "experience")
            if os.path.isdir(exp_dir):
                # Check for recent parquet files
                has_recent = False
                for f in os.listdir(exp_dir):
                    if f.endswith(".parquet"):
                        fpath = os.path.join(exp_dir, f)
                        if os.path.getmtime(fpath) > cutoff:
                            has_recent = True
                            break
                if has_recent:
                    relevant_dirs.append(exp_dir)
        
        return relevant_dirs

    def count_new_files(self, dirs: List[str]) -> int:
        count = 0
        cutoff = self.last_run_timestamp
        for d in dirs:
            for f in os.listdir(d):
                if f.endswith(".parquet"):
                    if os.path.getmtime(os.path.join(d, f)) > cutoff:
                        count += 1
        return count

    def run_training_cycle(self):
        if not self.config.get("enabled", False):
            logger.info("Auto-training is disabled.")
            return

        # Check safety throttles
        today = datetime.date.today()
        if today > self.last_day:
            self.deployed_today = 0
            self.last_day = today

        logger.info("Starting auto-train check...")
        exp_dirs = self.get_new_experience_dirs()
        new_file_count = self.count_new_files(exp_dirs)
        
        min_files = self.config.get("min_new_files", 5)
        if new_file_count < min_files:
            logger.info(f"Not enough new files ({new_file_count} < {min_files}). Skipping.")
            return

        logger.info(f"Found {new_file_count} new files in {len(exp_dirs)} directories. Launching training...")
        
        # Structured log: auto_train_start
        logger.info(json.dumps({
            "event": "auto_train_start",
            "symbol": self.config.get("symbol"),
            "new_files": new_file_count,
            "window_hours": self.config.get("train_window_hours")
        }))

        # Get current live model for parent_model argument
        live_model_path = ""
        live_json = os.path.join(self.paths.get("models_root", "models"), "live_model.json")
        if os.path.exists(live_json):
            try:
                with open(live_json, "r") as f:
                    data = json.load(f)
                    live_model_path = data.get("path", "")
            except:
                pass

        # Build command
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "offline_train.py"),
            "--symbol", self.config.get("symbol", "BTCUSDT"),
            "--steps", str(self.config.get("max_steps_per_cycle", 100000)),
            "--profile", self.config.get("feature_profile", "Rich"),
            "--exp_dirs"
        ] + exp_dirs
        
        if live_model_path:
            cmd += ["--load_model", live_model_path + ".zip"] # SB3 adds .zip

        try:
            subprocess.run(cmd, check=True)
            logger.info("Training process finished.")
            
            # Read latest registry entry
            self.process_training_result()
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Training failed with error: {e}")
        except Exception as e:
            logger.error(f"Error in training cycle: {e}")
        finally:
            self.last_run_timestamp = time.time()
            logger.info(json.dumps({"event": "auto_train_finished"}))

    def process_training_result(self):
        registry_root = self.paths.get("registry_root", "models/registry")
        if not os.path.exists(registry_root):
            logger.error("Registry root not found.")
            return

        # Find latest JSON
        files = [f for f in os.listdir(registry_root) if f.endswith(".json")]
        if not files:
            logger.warning("No registry entries found.")
            return

        files.sort(key=lambda x: os.path.getmtime(os.path.join(registry_root, x)), reverse=True)
        latest_file = os.path.join(registry_root, files[0])
        
        with open(latest_file, "r") as f:
            result = json.load(f)

        accepted = result.get("accepted", False)
        reason = result.get("reason", "Unknown")
        model_path = result.get("model_path", "")
        
        if not accepted:
            logger.info(json.dumps({
                "event": "auto_train_rejected",
                "reason": reason,
                "model_id": result.get("model_id")
            }))
            return

        # Accepted! Check throttle
        max_daily = self.config.get("max_models_per_day", 3)
        if self.deployed_today >= max_daily:
            logger.warning(f"Deployment limit reached for today ({self.deployed_today}/{max_daily}). Skipping reload.")
            return

        if self.config.get("dry_run", False):
            logger.info(f"Dry run enabled. Would have deployed {model_path}")
            return

        # Deploy via gRPC
        self.reload_policy(model_path)

    def reload_policy(self, model_path: str):
        try:
            # Add .zip if missing and it's a file
            if os.path.isfile(model_path + ".zip"):
                model_path += ".zip"
                
            channel = grpc.insecure_channel("localhost:50051")
            stub = bot_pb2_grpc.OrchestratorServiceStub(channel)
            
            req = bot_pb2.ReloadPolicyRequest(
                symbol=self.config.get("symbol", ""),
                model_path=model_path
            )
            
            resp = stub.ReloadPolicy(req)
            if resp.success:
                self.deployed_today += 1
                logger.info(json.dumps({
                    "event": "auto_train_deployed",
                    "model_path": model_path,
                    "deployed_today": self.deployed_today
                }))
            else:
                logger.error(f"ReloadPolicy failed: {resp.message}")
        except Exception as e:
            logger.error(f"Failed to call ReloadPolicy: {e}")

    def start_loop(self):
        logger.info("Entering Auto-Trainer main loop.")
        while True:
            self.load_config() # Refresh config every loop
            self.run_training_cycle()
            
            interval = self.config.get("interval_minutes", 60)
            logger.info(f"Sleeping for {interval} minutes...")
            time.sleep(interval * 60)

if __name__ == "__main__":
    trainer = AutoTrainer()
    trainer.start_loop()

import os
import json
import shutil
from datetime import datetime
from typing import Dict, Optional, List
import logging

class ModelRegistry:
    def __init__(self, registry_path: str = "models/registry", models_path: str = "models/live"):
        self.registry_path = registry_path
        self.models_path = models_path
        os.makedirs(registry_path, exist_ok=True)
        os.makedirs(models_path, exist_ok=True)
        self.logger = logging.getLogger("ModelRegistry")

    def register_model(self, 
                       model_path: str, 
                       metrics: Dict, 
                       parent_model_id: Optional[str], 
                       train_window: Dict,
                       feature_profile: str = "Rich") -> str:
        """
        Registers a new candidate model.
        Returns the model_id.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_id = f"model_{timestamp}"
        
        # 1. Copy model artifact to registry storage
        target_dir = os.path.join(self.models_path, model_id)
        os.makedirs(target_dir, exist_ok=True)
        
        # Assume model_path is a file (e.g. .zip or .pt) or directory
        if os.path.isdir(model_path):
            shutil.copytree(model_path, target_dir, dirs_exist_ok=True)
        else:
            shutil.copy2(model_path, target_dir)
            
        # 2. Create Metadata
        metadata = {
            "model_id": model_id,
            "parent_model_id": parent_model_id,
            "creation_time": timestamp,
            "feature_profile": feature_profile, # Added for Governance
            "timestamp": timestamp, # User requested
            "train_window": train_window,
            "metrics": metrics,
            "metrics_new": metrics, # User requested
            "model_path": target_dir, # User requested
            "status": "CANDIDATE", # CANDIDATE, ACCEPTED, REJECTED, ARCHIVED
            "accepted": False, # User requested
            "reason": None, # User requested
            "rejection_reason": None
        }
        
        self._save_metadata(model_id, metadata)
        self.logger.info(f"Registered model {model_id} as CANDIDATE")
        return model_id

    def judge_model(self, model_id: str, old_metrics: Dict, new_metrics: Dict, tolerance_pnl: float = -0.02, tolerance_dd: float = 0.01) -> bool:
        """
        Applies Acceptance Gate Logic.
        Returns True if accepted, False otherwise.
        """
        metadata = self._load_metadata(model_id)
        if not metadata:
            self.logger.error(f"Model {model_id} not found")
            return False
            
        # Comparison logic
        reasons = []
        accepted = True
        
        pnl_old = old_metrics.get("net_pnl", 0.0)
        pnl_new = new_metrics.get("net_pnl", 0.0)
        if pnl_new < pnl_old * (1.0 + tolerance_pnl): 
             accepted = False
             reasons.append(f"PnL regression: {pnl_new:.4f} vs {pnl_old:.4f}")

        dd_old = abs(old_metrics.get("max_dd", 0.0))
        dd_new = abs(new_metrics.get("max_dd", 0.0))
        if dd_new > dd_old + tolerance_dd: 
            accepted = False
            reasons.append(f"DD degradation: {dd_new:.4f} vs {dd_old:.4f}")
            
        tc_old = old_metrics.get("trade_count", 0)
        tc_new = new_metrics.get("trade_count", 0)
        if tc_new < tc_old * 0.5:
             accepted = False
             reasons.append(f"Trade count collapse: {tc_new} vs {tc_old}")

        # Update Metadata
        metadata["metrics_comparison"] = {
            "old": old_metrics,
            "new": new_metrics
        }
        metadata["metrics_old"] = old_metrics
        metadata["metrics_new"] = new_metrics
        metadata["accepted"] = accepted
        metadata["reason"] = "; ".join(reasons) if not accepted else "PnL and DD within tolerance"
        
        if accepted:
            metadata["status"] = "ACCEPTED"
            self.logger.info(f"Model {model_id} ACCEPTED")
        else:
            metadata["status"] = "REJECTED"
            metadata["rejection_reason"] = metadata["reason"]
            self.logger.warning(f"Model {model_id} REJECTED: {reasons}")
            
        self._save_metadata(model_id, metadata)
        return accepted

    def promote_to_live(self, model_id: str):
        """
        Marks model as LIVE and updates symlink/pointer.
        """
        metadata = self._load_metadata(model_id)
        if not metadata or metadata["status"] != "ACCEPTED":
             raise ValueError(f"Model {model_id} is not in ACCEPTED state")
             
        metadata["status"] = "LIVE"
        metadata["promoted_at"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._save_metadata(model_id, metadata)
        
        # Update "current_live" pointer
        live_ptr = os.path.join(self.models_path, "current_live")
        target = os.path.join(self.models_path, model_id)
        
        if os.path.exists(live_ptr):
            if os.path.islink(live_ptr):
                os.unlink(live_ptr)
            else:
                shutil.rmtree(live_ptr) # fallback if it was a dir copy
                
        # On Windows symlinks require admin, so maybe just copy or write a text pointer?
        # Python 3.8+ supports symlinks on Windows if Developer Mode is on.
        # Safer to write a "live_model.json" pointer file.
        with open(os.path.join(self.models_path, "live_model.json"), "w") as f:
            json.dump({"model_id": model_id, "path": target}, f, indent=2)
            
        self.logger.info(f"Promoted {model_id} to LIVE")

    def _save_metadata(self, model_id: str, metadata: Dict):
        path = os.path.join(self.registry_path, f"{model_id}.json")
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)

    def _load_metadata(self, model_id: str) -> Optional[Dict]:
        path = os.path.join(self.registry_path, f"{model_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

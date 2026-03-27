import json
import os
from typing import List, Dict, Any

class EpisodeBuilder:
    def __init__(self, index_path: str):
        self.index_path = index_path
        self.datasets = self._load_index()

    def _load_index(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading index: {e}")
        
        print("Index not found or invalid. Scanning filesystem...")
        return self._scan_filesystem()

    def _scan_filesystem(self) -> List[Dict[str, Any]]:
        datasets = []
        # Check specific known paths
        # Assuming script run from root, check 'data/runs', 'runs'
        search_paths = [
            os.path.join("data", "runs"),
            os.path.join("data", "runs", "runs"),
            "runs",
            os.path.join("runs", "runs")
        ]
        
        for root_path in search_paths:
            if not os.path.exists(root_path): continue
            
            for entry in os.scandir(root_path):
                if entry.is_dir():
                    # Check for datasets folder
                    ds_dir = os.path.join(entry.path, "datasets")
                    if os.path.exists(ds_dir):
                        for ds_entry in os.scandir(ds_dir):
                            if ds_entry.is_dir():
                                # Found a dataset candidate
                                ds_id = ds_entry.name
                                pq_path = os.path.join(ds_entry.path, "normalized_events.parquet")
                                if os.path.exists(pq_path):
                                    # Try to read manifest for metadata
                                    manifest_path = os.path.join(ds_entry.path, "dataset_manifest.json")
                                    quality_path = os.path.join(ds_entry.path, "quality_report.json")
                                    
                                    # Defaults
                                    symbol = "UNKNOWN"
                                    start_ts = 0
                                    end_ts = 0
                                    
                                    if os.path.exists(manifest_path):
                                        try:
                                            with open(manifest_path, 'r') as f:
                                                m = json.load(f)
                                                symbol = m.get("symbol", "UNKNOWN")
                                                start_ts = m.get("start_ts", 0)
                                                end_ts = m.get("end_ts", 0)
                                        except:
                                            pass
                                    
                                    # If symbol is still UNKNOWN, try quality report
                                    if symbol == "UNKNOWN" and os.path.exists(quality_path):
                                        try:
                                            with open(quality_path, 'r') as f:
                                                q = json.load(f)
                                                symbol = q.get("symbol", "UNKNOWN")
                                                # Use TS from quality only if we didn't get it from manifest
                                                if start_ts == 0: start_ts = q.get("start_ts", 0)
                                                if end_ts == 0: end_ts = q.get("end_ts", 0)
                                        except:
                                            pass
                                    
                                    # Fallback if unknown symbol (try to guess from run folder name?)
                                    # For now, just add it.
                                    datasets.append({
                                        "dataset_id": ds_id,
                                        "symbol": symbol,
                                        "start_ts": start_ts,
                                        "end_ts": end_ts,
                                        "usable_for_backtest": True, # Assume true for scanned
                                        "file_size_bytes": os.path.getsize(pq_path),
                                        "run_id": entry.name
                                    })
        
        print(f"Scanned {len(datasets)} datasets from filesystem.")
        return datasets

    def build_windows(self, 
                      symbols: List[str], 
                      window_len_secs: int = 1800, 
                      stride_secs: int = 300) -> List[Dict[str, Any]]:
        """
        Generates a list of episode windows for the given symbols.
        Only datasets marked usable_for_backtest=True are used.
        """
        episodes = []
        
        window_ms = window_len_secs * 1000
        stride_ms = stride_secs * 1000

        valid_datasets = [
            d for d in self.datasets 
            if d.get("usable_for_backtest", False) 
            and d.get("symbol") in symbols
            and d.get("file_size_bytes", 0) > 1024  # Filter out empty files
        ]

        print(f"Found {len(valid_datasets)} valid datasets for symbols {symbols}")

        for ds in valid_datasets:
            symbol = ds["symbol"]
            ds_id = ds["dataset_id"]
            start = ds["start_ts"]
            end = ds["end_ts"]
            
            # Simple slicing
            if symbol not in symbols:
                print(f"Skipping dataset {ds_id}: Symbol {symbol} not in {symbols}")
                continue
            if not ds.get("usable_for_backtest", False):
                print(f"Skipping dataset {ds_id}: Not usable for backtest")
                continue

            curr = start
            while curr + window_ms <= end:
                episodes.append({
                    "dataset_id": ds_id,
                    "symbol": symbol,
                    "start_ts": curr,
                    "end_ts": curr + window_ms,
                    "type": "train" # default
                })
                curr += stride_ms
                
        return episodes

if __name__ == "__main__":
    # Test
    import sys
    builder = EpisodeBuilder("data/index/datasets_index.json")
    windows = builder.build_windows(["BTCUSDT"])
    print(f"Generated {len(windows)} windows")
    if windows:
        print(windows[0])

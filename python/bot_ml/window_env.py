import gymnasium as gym
import numpy as np
from typing import List, Dict, Any
import bot_pb2
from grpc_env import GrpcTradingEnv

class WindowTradingEnv(GrpcTradingEnv):
    """
    Extension of GrpcTradingEnv that cycles through a list of episode windows.
    Each reset() picks the next window in the list.
    """
    def __init__(self, episodes: List[Dict[str, Any]], feature_profile: str = "Rich", **kwargs):
        super().__init__(feature_profile=feature_profile, **kwargs)
        self.episodes = episodes
        self.current_idx = 0
        if not self.episodes:
            print("Warning: No episodes provided to WindowTradingEnv")

    def reset(self, *, seed=None, options=None):
        if not self.episodes:
            return super().reset(seed=seed, options=options)
        
        # Pick next episode
        episode = self.episodes[self.current_idx]
        self.current_idx = (self.current_idx + 1) % len(self.episodes)
        
        # Update connection info for this episode
        self.dataset_id = episode['dataset_id']
        self.symbol = episode['symbol']
        
        if seed is not None:
            self.seed_val = seed

        # Construct ResetRequest with window bounds
        req = bot_pb2.ResetRequest(
            dataset_id=self.dataset_id,
            symbol=self.symbol,
            seed=self.seed_val,
            config=self.rl_config,
            start_ts=episode.get('start_ts', 0),
            end_ts=episode.get('end_ts', 0)
        )
        
        resp = self.stub.ResetEpisode(req)
        self.episode_id = resp.episode_id

        obs = np.array(resp.obs.vec, dtype=np.float32)
        info = {
            "episode_id": resp.episode_id,
            "equity": resp.state.equity if resp.state else 0.0,
            "ts": resp.obs.ts,
            "window_start": episode.get('start_ts', 0),
            "window_end": episode.get('end_ts', 0)
        }
        return obs, info

# Observation Schema V3

The BOTMK3 Feature Engine produces an **Observation Vector** for Machine Learning (RL) agents. Starting in **Schema V3**, the vector strictly separates values and their associated validity masks to prevent neural networks from hallucinating meaning out of missing/unwarmed data.

## 📐 Vector Shape & Layout
- **Total Dimensions (`OBS_DIM`)**: 70
- **Data Type**: `Vec<f32>`
- **Halves**: 
  - `Indices [0 .. 34]`: The 35 numerical features (clamped/normalized).
  - `Indices [35 .. 69]`: The 35 corresponding validity masks (`1.0` if valid, `0.0` if missing or unwarmed).

> [!WARNING]
> Policy servers MUST NEVER execute trades if critical liquidity masks (`mid_price`, `obi_top1`, `spread_bps`) are `0.0`. The system is in a state of Desync or startup.

## 📝 Feature Index Map

| Index `i` (Value) | Index `i+35` (Mask) | Feature Name | Normalization / Bounds | Fallback Value (if Mask=0) |
| :--- | :--- | :--- | :--- | :--- |
| **0** | **35** | `mid_price` | Raw `f64` cast to `f32` | `0.0` |
| **1** | **36** | `spread_abs` | Raw | `0.0` |
| **2** | **37** | `spread_bps` | Raw | `0.0` |
| **3** | **38** | `ret_1s` | `clamp(-1.0, 1.0)` | `0.0` |
| **4** | **39** | `ret_5s` | `clamp(-1.0, 1.0)` | `0.0` |
| **5** | **40** | `ret_30s` | `clamp(-1.0, 1.0)` | `0.0` |
| **6** | **41** | `rv_30s` | `clamp(-1.0, 1.0)` (Vol 30s) | `0.0` |
| **7** | **42** | `rv_5m` | `clamp(-1.0, 1.0)` (Vol 5m) | `0.0` |
| **8** | **43** | `taker_buy_vol_1s` | Raw | `0.0` |
| **9** | **44** | `taker_sell_vol_1s` | Raw | `0.0` |
| **10** | **45** | `taker_buy_vol_5s` | Raw | `0.0` |
| **11** | **46** | `taker_sell_vol_5s` | Raw | `0.0` |
| **12** | **47** | `tape_trades_1s` | Raw | `0.0` |
| **13** | **48** | `tape_intensity_z` | `clamp(-10.0, 10.0)` | `0.0` |
| **14** | **49** | `obi_top1` | `clamp(-1.0, 1.0)` | `0.0` |
| **15** | **50** | `obi_top3` | `clamp(-1.0, 1.0)` | `0.0` |
| **16** | **51** | `microprice` | Raw | `0.0` |
| **17** | **52** | `microprice_minus_mid_bps`| `clamp(-1.0, 1.0)` | `0.0` |
| **18** | **53** | `obi_delta_5s` | `clamp(-1.0, 1.0)` | `0.0` |
| **19** | **54** | `liq_buy_vol_30s` | Raw | `0.0` |
| **20** | **55** | `liq_sell_vol_30s` | Raw | `0.0` |
| **21** | **56** | `liq_net_30s` | Raw | `0.0` |
| **22** | **57** | `liq_count_30s` | Raw | `0.0` |
| **23** | **58** | `mark_minus_mid_bps` | `clamp(-1.0, 1.0)` | `0.0` |
| **24** | **59** | `funding_rate` | `clamp(-1.0, 1.0)` | `0.0` |
| **25** | **60** | `ema200_distance_pct` | `clamp(-1.0, 1.0)` | `0.0` |
| **26** | **61** | `rsi_14` | Raw `f32` (0-100 scale) | **`50.0`** (Neutral RSI) |
| **27** | **62** | `bb_width` | Raw | `0.0` |
| **28** | **63** | `bb_pos` | `clamp(-10.0, 10.0)` | `0.0` |
| **29** | **64** | `position_flag` | Raw `-1.0, 0.0, 1.0` | `0.0` |
| **30** | **65** | `latent_pnl_pct` | `clamp(-1.0, 1.0)` | `0.0` |
| **31** | **66** | `max_pnl_pct` | `clamp(-1.0, 1.0)` | `0.0` |
| **32** | **67** | `current_drawdown_pct` | `clamp(-1.0, 1.0)` | `0.0` |
| **33** | **68** | `time_sin` | Raw | `0.0` |
| **34** | **69** | `time_cos` | Raw | `0.0` |

## 🛡️ Python Integration (SB3/PPO)

The Python Policy Server receives `obs` as an array of length 70 via gRPC. 
To feed this explicitly to custom feature extractors in SB3 (like a masking layer):

```python
values = obs[:35]
masks = obs[35:]
masked_input = values * masks  # Zero-out structurally, ensuring no false signals
```

The Handshake guard in `bot-server` strictly rejects connections if the Python server's `profile` endpoint returns `schema_version != 3` or `obs_dim != 70`.

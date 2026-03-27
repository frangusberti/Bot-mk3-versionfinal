import requests
import time

def test_contract():
    url = "http://localhost:50055/infer"
    payload = {
        "symbol": "BTCUSDT",
        "ts_ms": int(time.time() * 1000),
        "mode": "PAPER",
        "decision_interval_ms": 1000,
        "obs": [0.1] * 12,
        "risk": {"max_pos_frac": 0.5, "effective_leverage": 10.0},
        "portfolio": {
            "is_long": 0.0,
            "is_short": 0.0,
            "is_flat": 1.0,
            "position_frac": 0.0,
            "upnl_frac": 0.0,
            "leverage_used": 0.0,
            "equity": 1500.0,
            "cash": 1500.0
        },
        "meta": {}
    }
    
    print("Testing /infer contract...")
    try:
        resp = requests.post(url, json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.json()}")
        assert resp.status_code == 200
        assert "action" in resp.json()
        print("Contract Test PASSED")
    except Exception as e:
        print(f"Contract Test FAILED: {e}")

if __name__ == "__main__":
    test_contract()

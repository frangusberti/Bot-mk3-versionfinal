import json

lines = open(r"C:\Bot mk3\pricing_audit.jsonl").readlines()
n = len(lines)
buys = sells = 0
buy_gt_mid = sell_lt_mid = buy_gt_bid = sell_lt_ask = 0
offsets = []; spreads = []
samples = []

for line in lines:
    d = json.loads(line.strip())
    if d["side"] == "Buy": buys += 1
    else: sells += 1
    if d["buy_gt_mid"]: buy_gt_mid += 1
    if d["sell_lt_mid"]: sell_lt_mid += 1
    if d["buy_gt_bid"]: buy_gt_bid += 1
    if d["sell_lt_ask"]: sell_lt_ask += 1
    offsets.append(d["offset_bps"])
    spreads.append(d["spread_bps"])
    
    # Compute spread capture vs mid: for buy, sc = (mid - posted) / mid * 10000
    mid = d["mid"]
    pp = d["posted_price"]
    if d["side"] == "Buy":
        sc = (mid - pp) / mid * 10000 if mid > 0 else 0
    else:
        sc = (pp - mid) / mid * 10000 if mid > 0 else 0
    
    # Compute distance to BBO
    if d["side"] == "Buy":
        dist_bbo = (d["best_bid"] - pp) / mid * 10000 if mid > 0 else 0
    else:
        dist_bbo = (pp - d["best_ask"]) / mid * 10000 if mid > 0 else 0
    
    d["sc_vs_mid"] = round(sc, 2)
    d["dist_to_bbo"] = round(dist_bbo, 2)
    samples.append(d)

print(f"Total orders posted: {n}")
print(f"  Buys: {buys}  Sells: {sells}")
print()
print("ANOMALY CHECK:")
print(f"  buy_price > mid:       {buy_gt_mid}/{buys}")
print(f"  sell_price < mid:      {sell_lt_mid}/{sells if sells > 0 else 'N/A'}")
print(f"  buy_price > best_bid:  {buy_gt_bid}/{buys}")
print(f"  sell_price < best_ask: {sell_lt_ask}/{sells if sells > 0 else 'N/A'}")
print()
print(f"Offset bps: avg={sum(offsets)/n:.2f} min={min(offsets):.2f} max={max(offsets):.2f}")
print(f"Spread bps: avg={sum(spreads)/n:.2f} min={min(spreads):.2f} max={max(spreads):.2f}")
print()

scs = [s["sc_vs_mid"] for s in samples]
dists = [s["dist_to_bbo"] for s in samples]
print(f"Spread capture vs mid: avg={sum(scs)/len(scs):.2f} bps")
print(f"Distance to BBO:       avg={sum(dists)/len(dists):.2f} bps (negative = behind BBO)")
print()

# Show first 10 samples
print("SAMPLE (first 10):")
print(f"{'Side':>5} {'Mid':>10} {'Bid':>10} {'Ask':>10} {'Posted':>10} {'Offset':>6} {'SC_mid':>6} {'DistBBO':>7}")
for s in samples[:10]:
    print(f"{s['side']:>5} {s['mid']:>10.2f} {s['best_bid']:>10.2f} {s['best_ask']:>10.2f} {s['posted_price']:>10.2f} {s['offset_bps']:>6.2f} {s['sc_vs_mid']:>6.2f} {s['dist_to_bbo']:>7.2f}")

# Show any sells
sell_samples = [s for s in samples if s["side"] == "Sell"]
if sell_samples:
    print(f"\nSELL SAMPLES ({len(sell_samples)} total):")
    for s in sell_samples[:5]:
        print(f"  mid={s['mid']:.2f} bid={s['best_bid']:.2f} ask={s['best_ask']:.2f} posted={s['posted_price']:.2f} offset={s['offset_bps']:.2f} sc={s['sc_vs_mid']:.2f}")

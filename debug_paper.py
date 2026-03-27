from bot_ml.paper_account import PaperAccount

p = PaperAccount()
print(f"Start: {p.balance}, {p.position_qty}")
p.step(60000)
print(f"Equity 0: {p.equity_curve[-1]}")

p.apply_fill("Buy", 60000, 0.016, True)
print(f"After Buy: {p.balance}, {p.position_qty}, {p.avg_entry_price}")

p.step(60000)
print(f"Equity 1: {p.equity_curve[-1]}")

p.step(66000)
print(f"Equity 2: {p.equity_curve[-1]}")

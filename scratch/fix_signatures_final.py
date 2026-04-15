
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Fix get_synthetic_passive_price signature
orig = orig.replace("fn get_synthetic_passive_price(&self, side: Side) -> Option<f64> {", "fn get_synthetic_passive_price(&self, side: Side, is_exit: bool) -> Option<f64> {")

# 2. Fix submit_passive_order calls that might be missing is_exit
# Searching for pattern: self.submit_passive_order(side, qty)
orig = re.sub(r"self\.submit_passive_order\((Side::[A-Za-z]+, [a-z_]+)\)", r"self.submit_passive_order(\1, false)", orig)

# 3. Ensure submit_passive_order signature is correct (3 args)
# fn submit_passive_order(&mut self, side: Side, qty: f64, is_exit: bool) -> u32
if "fn submit_passive_order(&mut self, side: Side, qty: f64) -> u32 {" in orig:
    orig = orig.replace("fn submit_passive_order(&mut self, side: Side, qty: f64) -> u32 {", "fn submit_passive_order(&mut self, side: Side, qty: f64, is_exit: bool) -> u32 {")

# 4. Fix call to get_synthetic_passive_price inside submit_passive_order
# It was likely self.get_synthetic_passive_price(side, false) but let's be sure.
orig = orig.replace("self.get_synthetic_passive_price(side, false)", "self.get_synthetic_passive_price(side, is_exit)")

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(orig.replace('\n', '\r\n'))

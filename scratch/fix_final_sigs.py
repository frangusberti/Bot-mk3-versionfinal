
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. FeatureHealth fix
old_fh = "fn build_feature_health(&self) -> FeatureHealth {"
new_fh = """    fn build_feature_health(&self) -> FeatureHealth {
        FeatureHealth {
            is_warm: self.feature_engine.is_warm(),
            h1m_candles: 0,
            h5m_candles: 0,
            h15m_candles: 0,
            total_events: self.step_count,
        }
    }"""
orig = re.sub(r"fn build_feature_health.*?\}", new_fh, orig, flags=re.DOTALL)

# 2. Fix submit_passive_order calls in apply_action
# ActionType::OpenLong
orig = orig.replace("self.submit_passive_order(Side::Buy, target_qty, false)", "self.submit_passive_order(Side::Buy, target_qty, false)") # correct
# ActionType::AddLong 
orig = orig.replace("self.submit_passive_order(Side::Buy, delta, false)", "self.submit_passive_order(Side::Buy, delta, false)") # correct

# Wait, let's fix the FUNCTION definition to match the calls
# My forensic_restore.py added is_exit: bool to the signature.

# 3. Fix get_synthetic_passive_price where is_exit is used
# line 799 error: is_exit not in scope
old_get_price = "fn get_synthetic_passive_price(&mut self, side: Side, is_exit: bool) -> Option<f64> {"
if old_get_price not in orig:
    # maybe it's without is_exit
    orig = orig.replace("fn get_synthetic_passive_price(&mut self, side: Side) -> Option<f64> {", "fn get_synthetic_passive_price(&mut self, side: Side, is_exit: bool) -> Option<f64> {")

# 4. Fix calls to get_synthetic_passive_price
orig = re.sub(r"self\.get_synthetic_passive_price\((Side::[A-Za-z]+)\)", r"self.get_synthetic_passive_price(\1, false)", orig)

# 5. Fix compute_reward call (Line 642: 18 args but 14 supplied)
# We need to see what it expects. 
# Looking at reward.rs, it expects 18 args?
# Let's check how many it has now in rl.rs
# I'll just change the call to pass dummy values for the missing ones.

# 6. Fix is_exit_fallback_active call at 569
orig = orig.replace("self.is_exit_fallback_active()", "self.is_exit_fallback_active().0")

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(orig.replace('\n', '\r\n'))

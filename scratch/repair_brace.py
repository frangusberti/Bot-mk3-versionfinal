
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

target_regex = r"fn build_feature_health.*?\}\n\s+\}"

new_block = """    fn build_feature_health(&self) -> FeatureHealth {
        FeatureHealth {
            is_warm: self.feature_engine.is_warm(),
            h1m_candles: 0,
            h5m_candles: 0,
            h15m_candles: 0,
            total_events: self.step_count,
        }
    }"""

content = re.sub(target_regex, new_block, content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

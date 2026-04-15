
import os

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

# 1. Fix hold_ms type inference
if "(now_ts - start_ts).max(0) as u64;" in content:
    content = content.replace("(now_ts - start_ts).max(0) as u64;", "(now_ts - start_ts).max(0i64) as u64;")

# 2. Asegurarse de que no estamos usando use_winner_unlock en ningún lugar
content = content.replace("use_winner_unlock: cfg.use_winner_unlock,", "")

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

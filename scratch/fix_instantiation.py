
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

# Actualizar el bloque let mut episode = EpisodeHandle { ... }
new_init_fields = """            accepted_as_marketable_count: 0,
            accepted_as_passive_count: 0,
            resting_fill_count: 0,
            immediate_fill_count: 0,
            liquidity_flag_unknown_count: 0,"""

# Buscamos el final de la instanciación (antes de };)
if "realized_pnl_total: 0.0," in content:
    content = content.replace("realized_pnl_total: 0.0,", "realized_pnl_total: 0.0,\n" + new_init_fields)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

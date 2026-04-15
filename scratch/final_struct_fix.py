
import os

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

# Añadir campos faltantes a la definición del struct
struct_target = "    pub exit_maker_pricing_multiplier: f32,"
missing_fields = """
    pub accepted_as_marketable_count: u32,
    pub accepted_as_passive_count: u32,
    pub resting_fill_count: u32,
    pub immediate_fill_count: u32,
    pub liquidity_flag_unknown_count: u32,
"""

if struct_target in content:
    content = content.replace(struct_target, struct_target + missing_fields)

# También asegurar que se inicializan en 0 en StepResponse reset
reset_target = "episode.entry_veto_count_in_step = 0;"
reset_fields = """
        episode.accepted_as_marketable_count = 0;
        episode.accepted_as_passive_count = 0;
        episode.resting_fill_count = 0;
        episode.immediate_fill_count = 0;
        episode.liquidity_flag_unknown_count = 0;
"""

if reset_target in content:
    content = content.replace(reset_target, reset_target + reset_fields)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

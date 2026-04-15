
import os

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

content = "".join(lines)

# Re-construcción del bloque compute_action_mask que fue borrado accidentalmente
# El bloque debería empezar después de: let mut mask = [1.0f32; 10]; 

wrong_content = """    fn compute_action_mask(&mut self) -> [f32; 10] {
        let mut mask = [1.0f32; 10]; 
        let sell_marketable = if let Some(price) = self.get_synthetic_passive_price(Side::Sell, false) {"""

restored_content = """    fn compute_action_mask(&mut self) -> [f32; 10] {
        let mut mask = [1.0f32; 10]; 
        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let has_pos = current_pos.is_some() && current_pos.unwrap().qty > 1e-9;
        let pos_side = current_pos.map(|p| p.side);
        let _active_orders_count = self.exec_engine.portfolio.state.active_orders.len();

        let (fallback_active, _) = self.is_exit_fallback_active();
        self.exit_fallback_triggered_in_step = fallback_active;

        // Maker Regime Marketability Checks
        let buy_marketable = if let Some(price) = self.get_synthetic_passive_price(Side::Buy, false) {
            self.exec_engine.book_asks.first().map_or(false, |ask| price >= ask.0 - 1e-7)
        } else { true };
        let sell_marketable = if let Some(price) = self.get_synthetic_passive_price(Side::Sell, false) {"""

# Normalización
wrong_content = wrong_content.replace('\n', '\r\n')
restored_content = restored_content.replace('\n', '\r\n')

if wrong_content in content:
    content = content.replace(wrong_content, restored_content)
    print("SUCCESS: restored and fixed compute_action_mask")
else:
    print("ERROR: could not find wrong_content. Already fixed or different?")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

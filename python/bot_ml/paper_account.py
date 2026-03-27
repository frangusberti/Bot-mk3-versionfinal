import numpy as np

class PaperAccount:
    """Realistic Paper Trading Account for Bot Evaluation."""
    def __init__(self, initial_balance=10000.0, fixed_notional=1000.0, maker_fee=0.0002, taker_fee=0.0005):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.fixed_notional = fixed_notional
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        
        self.position_qty = 0.0 # Positive for Long, Negative for Short
        self.avg_entry_price = 0.0
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.unrealized_pnl = 0.0
        
        self.equity_curve = []
        self.max_equity = initial_balance
        self.max_drawdown = 0.0
        
        # Trade metrics
        self.trades = [] # List of closed trade results (pnl)
        self.exposure_steps = 0
        
    def step(self, current_mid):
        """Update unrealized PnL and Equity Curve at each step."""
        if self.position_qty != 0:
            self.unrealized_pnl = self.position_qty * (current_mid - self.avg_entry_price)
            self.exposure_steps += 1
        else:
            self.unrealized_pnl = 0.0
            
        current_equity = self.balance + self.unrealized_pnl
        self.equity_curve.append(float(current_equity))
        
        # Update Drawdown
        if current_equity > self.max_equity:
            self.max_equity = current_equity
        
        drawdown = (self.max_equity - current_equity) / self.max_equity
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
            
    def apply_fill(self, side, price, qty_base, is_maker):
        """Apply a trade fill to the account."""
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        
        # In this model, we use fixed notional (1000 USDT)
        # However, the environment gives us qty_base from its internal sim.
        # To keep it "Paper", we'll scale the fill to our fixed notional if qty_base is a 'unit' fill.
        # But wait, the bot's action already implies a side. 
        # For simplicity, we'll assume 1 fill = 1000 USDT notional move.
        
        notional_filled = self.fixed_notional
        fill_qty = notional_filled / price
        
        fee_usdt = notional_filled * fee_rate
        self.total_fees += fee_usdt
        self.balance -= fee_usdt # Account for fees only
        
        if side == "Buy":
            new_qty = self.position_qty + fill_qty
            # Check for closing/reducing short
            if self.position_qty < 0:
                closed_qty = min(abs(self.position_qty), fill_qty)
                realized = closed_qty * (self.avg_entry_price - price)
                self.realized_pnl += realized
                self.balance += realized # Add realized gain/loss
                self.trades.append(float(realized))
            
            # Update average price if increasing long
            if new_qty > 0 and new_qty > self.position_qty:
                if self.position_qty >= 0:
                    self.avg_entry_price = (self.avg_entry_price * self.position_qty + price * fill_qty) / new_qty
                else:
                    # Switched from short to long
                    self.avg_entry_price = price
            
            self.position_qty = new_qty
            
        else: # Sell
            new_qty = self.position_qty - fill_qty
            # Check for closing/reducing long
            if self.position_qty > 0:
                closed_qty = min(self.position_qty, fill_qty)
                realized = closed_qty * (price - self.avg_entry_price)
                self.realized_pnl += realized
                self.balance += realized # Add realized gain/loss
                self.trades.append(float(realized))
                
            # Update average price if increasing short
            if new_qty < 0 and new_qty < self.position_qty:
                if self.position_qty <= 0:
                    self.avg_entry_price = (self.avg_entry_price * abs(self.position_qty) + price * fill_qty) / abs(new_qty)
                else:
                    # Switched from long to short
                    self.avg_entry_price = price
            
            self.position_qty = new_qty

    def get_report(self):
        """Return full economic report."""
        final_equity = self.balance + self.unrealized_pnl
        net_return = (final_equity - self.initial_balance) / self.initial_balance
        
        wins = [t for t in self.trades if t > 0]
        losses = [t for t in self.trades if t <= 0]
        
        win_rate = len(wins) / len(self.trades) if self.trades else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        
        return {
            "initial_balance": self.initial_balance,
            "final_equity": float(final_equity),
            "net_return_pct": float(net_return * 100),
            "max_drawdown_pct": float(self.max_drawdown * 100),
            "total_realized_pnl": float(self.realized_pnl),
            "total_unrealized_pnl": float(self.unrealized_pnl),
            "total_fees": float(self.total_fees),
            "total_trades_count": len(self.trades),
            "win_rate": float(win_rate),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "profit_factor": float(sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0),
            "exposure_time_pct": float(self.exposure_steps / len(self.equity_curve) * 100 if self.equity_curve else 0),
            "equity_curve": self.equity_curve
        }

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from stat_arb.StatArb_Parent import StatArb_Parent


class StatArb_LimitLimit(StatArb_Parent):
    """Update the remaining limit orders after an initial fill."""

    async def send_follow_up_orders(
        self, objs_list, filled_obj, filled_trade, filled_buy_sell_lower
    ):
        """Use the filled leg's net cash flow to reprice the remaining legs."""

        buy_sell_scalar = -1 if filled_buy_sell_lower == "buy" else 1
        size_per_unit = filled_obj.scalar_size_FIs_per_unit
        avg_filled_price = filled_trade.orderStatus.avgFillPrice
        trade_cf = buy_sell_scalar * avg_filled_price * size_per_unit

        # Preserve the existing fixed commission estimate per instrument.
        comm_amt = -0.0055  # could be a getattr on the filled_trade, but for now it's hardcoded
        comm_cf = comm_amt * size_per_unit
        
        input_cf = trade_cf + comm_cf + getattr(filled_obj, "div_adj_unit_cf", 0)

        for output_obj in objs_list:
            await self.update_trade_details(output_obj, input_cf)

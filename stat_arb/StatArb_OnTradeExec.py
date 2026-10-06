import asyncio


class StatArb_OnTradeExec:
    """Handle trade fills and update the remaining orders."""


    def on_trade_exec(self, filled_obj, filled_trade):
        """Ignore trades until completely filled and dispatch fills to the appropriate handler."""
        if filled_trade.orderStatus.status != "Filled":
            return

        if filled_obj.strat_on_mkt_data_change:
            self._on_first_fill(filled_obj, filled_trade)
        else:
            self._on_second_fill(filled_obj, filled_trade)


    def _on_first_fill(self, filled_obj, filled_trade):
        """Schedule follow-up orders and stop repricing on market-data changes."""
        side = filled_trade.order.action.lower()
        remaining_objs = filled_obj.rest_of_objs_list

        self.follow_up_orders_task = asyncio.create_task(
            self.send_follow_up_orders(remaining_objs, filled_obj, filled_trade, side)
        )
        
        for obj in remaining_objs:
            obj.strat_on_mkt_data_change = False

        filled_obj.strat_on_mkt_data_change = False
        self._finished_order_admin(filled_obj, filled_trade)


    def _on_second_fill(self, filled_obj, filled_trade):
        """Cancel remaining orders on the filled side and disable fill handling."""

        for obj in self.objs_list:
            obj.strat_on_trade_exec = False

        self._finished_order_admin(filled_obj, filled_trade)

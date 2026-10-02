import asyncio


class StatArb_OnTradeExec:
    """Handle trade fills and update the remaining orders."""


    def on_trade_exec(self, filled_obj, filled_trade):
        """Ignore unfilled trades and dispatch fills to the appropriate handler."""
        if filled_trade.orderStatus.filled == 0:
            return

        if filled_obj.strat_on_mkt_data_change:
            self._on_first_fill(filled_obj, filled_trade)
        else:
            self._on_second_fill(filled_obj, filled_trade)


    def _on_first_fill(self, filled_obj, filled_trade):
        """Schedule follow-up orders and stop repricing on market-data changes."""
        side = filled_trade.order.action.lower()
        remaining_objs = filled_obj.rest_of_objs_list

        if self.g_or_p == "group":
            hit_lift = "hit_bid" if side == "buy" else "lift_ask"
            follow_up_objs = getattr(self.bo_obj, f"strat_{hit_lift}_ranked_objs_list")
        else:
            follow_up_objs = remaining_objs

        self.follow_up_orders_task = asyncio.create_task(
            self.send_follow_up_orders(follow_up_objs, filled_obj, filled_trade, side)
        )

        trade_attr = f"active_{side}_trade"
        for obj in remaining_objs:
            order_to_cancel = getattr(obj, trade_attr)
            self.cancel_order(obj, order_to_cancel)
            obj.strat_on_mkt_data_change = False

        filled_obj.strat_on_mkt_data_change = False
        self._finished_order_admin(filled_obj, filled_trade)


    def _on_second_fill(self, filled_obj, filled_trade):
        """Cancel remaining orders on the filled side and disable fill handling."""
        side = filled_trade.order.action.lower()
        trade_attr = f"active_{side}_trade"

        for obj in self.objs_list:
            order_to_cancel = getattr(obj, trade_attr)
            self.cancel_order(obj, order_to_cancel)
            obj.strat_on_trade_exec = False

        self._finished_order_admin(filled_obj, filled_trade)

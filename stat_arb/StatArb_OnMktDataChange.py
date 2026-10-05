import asyncio


class StatArb_OnMktDataChange:
    """Batch market-data changes and reprice the affected limit orders."""

    def prepare_on_mkt_data_change(self):
        self.pending_mkt_data_objs = set()
        self.need_to_update = False
        self.is_update_in_progress = False
        self.update_task = None


    def on_mkt_data_change(self, obj):
        """Queue an object, starting a worker only when none is running."""
        self.pending_mkt_data_objs.add(obj)
        self.need_to_update = True

        if self.is_update_in_progress:
            return

        self.is_update_in_progress = True
        self.update_task = asyncio.create_task(self._update_trade_worker())


    async def _update_trade_worker(self):
        """Process each pending object using its latest market data."""
        try:
            while True:
                self.need_to_update = False
                pending_objs = list(self.pending_mkt_data_objs)
                self.pending_mkt_data_objs.clear()

                for obj in pending_objs:
                    await self._update_trade(obj)

                if not self.need_to_update:
                    break
        finally:
            self.is_update_in_progress = False


    async def _update_trade(self, updated_obj):
        """Reprice group orders or the other legs of a pair."""
        if not updated_obj.is_mkt_data_valid():
            return

        trade_cf = getattr(updated_obj, updated_obj.input_price_attr)
        comm_cf = getattr(updated_obj, updated_obj.input_comm_attr)
        input_cf = trade_cf + comm_cf + getattr(updated_obj, "div_adj_unit_cf", 0)

        for output_obj in updated_obj.rest_of_objs_list:
            await self.update_trade_details(output_obj, input_cf)


    async def update_trade_details(self, output_obj, input_cf, *, buy_sell=None):
        """Reprice an order from net input cash flow and its profit margin."""
        if not output_obj.is_mkt_data_valid():
            return

        if buy_sell is None:
            buy_sell = output_obj.buy_or_sell.lower()

        active_order_input = output_obj.active_order_input
        if abs(active_order_input - input_cf) < 1e-9:
            return

        profitable_unit_cf = (
            output_obj.profit_margin - input_cf - getattr(output_obj, "div_adj_unit_cf", 0)
        )
        new_order_price, _ = output_obj.decompose_unit_cf(profitable_unit_cf, "taker")
        new_order_price = output_obj.round_price_to_tick(
            abs(new_order_price), buy_sell.upper()
        )

        active_order_price = getattr(output_obj, f"active_order_price")
        if abs(active_order_price - new_order_price) < 1e-9:
            return

        active_trade = getattr(output_obj, f"active_trade")
        new_trade = self.update_limit_order(
            obj=output_obj, trade=active_trade, price=new_order_price
        )
        if new_trade is not None:
            self._placed_order_admin(output_obj, new_trade, input_cf)

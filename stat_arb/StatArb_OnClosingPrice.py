class StatArb_OnClosingPrice:
    """Place placeholder limit orders using the market closing price."""

    def on_closing_price(self, obj):
        """Disable closing-price handling once all required orders are placed."""
        closing_price = obj.price_screen_close
        sides = (
            ("BUY", "SELL")
            if self.g_or_p == "group"
            else (obj.buy_or_sell.upper(),)
        )
        all_orders_placed = True

        for side in sides:
            if side == "BUY":
                price = closing_price * 0.5
                size = obj.buy_size
            else:
                price = closing_price * 2.0
                size = obj.sell_size

            price = obj.round_price_to_tick(price, side)
            trade = self.update_limit_order(
                obj=obj,
                buy_sell=side,
                price=price,
                size=size,
            )
            if trade is None:
                all_orders_placed = False
                continue

            self._placed_order_admin(obj, trade, closing_price)

        if all_orders_placed:
            obj.strat_on_closing_price = False

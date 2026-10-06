class StatArb_OnClosingPrice:
    """Place placeholder limit orders using the market closing price."""

    def on_closing_price(self, obj):
        """Disable closing-price handling once all required orders are placed."""
        closing_price = obj.price_screen_close
        sides = [obj.buy_or_sell.upper()]
        
        all_orders_placed = True

        for side in sides:
            if side == "BUY":
                price = closing_price * 0.5
            else:
                price = closing_price * 2.0

            price = closing_price * 0.5 if side == "BUY" else closing_price * 2.0
            price = obj.round_price_to_tick(price, side)
            
            trade = self.update_limit_order(
                obj=obj,
                buy_sell=side,
                price=price,
                size=obj.size
            )
            if trade is None:
                all_orders_placed = False
                continue

            self._placed_order_admin(obj, trade, closing_price)

        if all_orders_placed:
            obj.strat_on_closing_price = False

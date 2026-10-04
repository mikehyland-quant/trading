import asyncio
import math
from types import MethodType

import winsound


class Strategy_Parent:
    """Bind strategy helpers to instruments and manage their orders."""

    def __init__(self, objs_list=None, *args, **kwargs):
        super().__init__()
        self.done_event = asyncio.Event()
        self.need_to_print_active_orders = True
        self.need_to_print_finished_orders = True
        self.objs_list = objs_list if objs_list is not None else []

        for obj in self.objs_list:
            obj.strategy = self
            obj.strat_on_closing_price = True
            obj.strat_on_mkt_data_change = True
            obj.strat_on_trade_exec = True
            obj.round_price_to_tick = MethodType(type(self).round_price_to_tick, obj)
            obj.round_size_to_increment = MethodType(
                type(self).round_size_to_increment, obj
            )

    @classmethod
    def play_fill_sound(cls):
        """Play the Windows fill notification without blocking."""
        winsound.PlaySound(
            r"C:\Windows\Media\notify.wav",
            winsound.SND_FILENAME | winsound.SND_ASYNC,
        )

    def round_price_to_tick(self, price, buy_sell=None):
        """Round buys down and sells up to a tick; supply an absolute price."""
        if price is None:
            return None

        price = self._safe_float(price, default=None)
        tick = self._safe_float(self.min_tick, default=None)
        if buy_sell:
            buy_sell = buy_sell.upper()
        elif hasattr(self, "buy_sell"):
            buy_sell = self.buy_sell.upper()
        else:
            buy_sell = None

        if price is None or buy_sell not in ("BUY", "SELL"):
            return None
        if tick in (None, 0):
            return price

        round_to_tick = math.floor if buy_sell == "BUY" else math.ceil
        return round(round_to_tick(price / tick) * tick, 10)

    def round_size_to_increment(self, size):
        """Round an absolute size to its increment, returning zero below minimum."""
        if size is None:
            return None
        size = self._safe_float(size, default=None)
        if size is None:
            return None

        increment = self.size_increment
        minimum_size = self.min_size
        rounded = size if increment in (None, 0) else round(size / increment) * increment
        if minimum_size not in (None, 0) and rounded < minimum_size:
            return 0.0
        return round(rounded, 10)

    def update_market_order(self, obj=None, size=None, buy_sell=None, trade=None):
        """Place a market order or convert an existing trade to one."""
        if trade is None:
            return obj.platform_obj.place_market_order(
                obj=obj, size=size, buy_sell=buy_sell
            )
        return obj.platform_obj.modify_to_market_order(
            obj=obj, size=size, buy_sell=buy_sell, trade=trade
        )

    def update_limit_order(
        self, obj=None, size=None, buy_sell=None, trade=None, price=None, all_or_none=None
    ):
        """Place or modify a limit order through the instrument's platform."""
        if trade is None:
            if all_or_none is None:
                all_or_none = False
            return obj.platform_obj.place_limit_order(
                obj=obj,
                size=size,
                buy_sell=buy_sell,
                price=price,
                all_or_none=all_or_none,
            )
        return obj.platform_obj.modify_limit_order(
            obj=obj,
            size=size,
            buy_sell=buy_sell,
            trade=trade,
            price=price,
            all_or_none=all_or_none,
        )

    def cancel_order(self, obj, trade):
        """Cancel a trade when one exists."""
        if trade is not None:
            obj.platform_obj.cancel_order(trade)

    def print_orders(self, active_finished, buy_sell, size, fi_name, price, order_id):
        """Print an order summary, labeling orders without a price as market orders."""
        display_price = "market" if price is None else price
        print(
            f"{active_finished} order: {buy_sell} {size} of {fi_name} "
            f"at {display_price} - order_id: {order_id}",
            "\n",
        )

    def finish_strategy(self):
        """Finalize results before notifying waiters that the strategy is done."""
        self._finalize_results()
        if self.done_event is not None:
            self.done_event.set()

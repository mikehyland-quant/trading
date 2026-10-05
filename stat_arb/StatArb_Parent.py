from ib_insync import obj

from stat_arb.StatArb_OnClosingPrice import StatArb_OnClosingPrice
from stat_arb.StatArb_OnMktDataChange import StatArb_OnMktDataChange
from stat_arb.StatArb_OnTradeExec import StatArb_OnTradeExec
from strategies.Strategy_Parent import Strategy_Parent


class StatArb_Parent(
    StatArb_OnClosingPrice,
    StatArb_OnMktDataChange,
    StatArb_OnTradeExec,
    Strategy_Parent,
):
    """Initialize stat-arb legs and maintain order state and trade results."""


    def __init__(self, profit_margin, objs_list): # bo_obj_or_objs_list):
        self.profit_margin = profit_margin
        super().__init__(objs_list)
        self.prepare_on_mkt_data_change()

        for obj in self.objs_list:
            obj.rest_of_objs_list = [other for other in objs_list if other is not obj]

            obj.active_trade = None
            obj.active_order_input = None
            obj.active_order_price = None  

            if self.g_or_p == "pair":
                obj.buy_or_sell = obj.buy_or_sell.upper()
                is_buy = obj.buy_or_sell == "BUY"
                obj.input_price_attr = "cf_unit_lift_ask" if is_buy else "cf_unit_hit_bid"
                obj.div_adj_cf = -obj.div_adj if is_buy else obj.div_adj
                obj.div_adj_unit_cf = obj.div_adj_cf * obj.scalar_size_FIs_per_unit
                obj.input_comm_attr = f"{obj.input_price_attr}_comm"
                

    def _placed_order_admin(self, obj, trade, input_amt):
        """Record a placed order and optionally print its details."""
        order = trade.order

        obj.active_trade = trade
        obj.active_order_input = input_amt
        obj.active_order_price = order.lmtPrice  

        if self.need_to_print_active_orders:
            self.print_orders(
                "active",
                order.action.lower(),
                order.totalQuantity,
                obj.my_fi_name,
                order.lmtPrice,
                order.orderId,
            )


    def _finished_order_admin(self, obj, trade):
        """Record a filled leg and finish when no legs need execution handling."""
        obj.active_trade = trade
        
        order = trade.order
        status = trade.orderStatus
        side = order.action.lower()

        if side == "buy":
            self.buy_obj = obj
        else:
            self.sell_obj = obj

        filled_FIs = status.filled
        obj.filled_trading_size += filled_FIs
        obj.remaining_trading_size -= filled_FIs

        if self.need_to_print_finished_orders:
            self.print_orders(
                "finished",
                order.action,
                status.filled,
                obj.my_fi_name,
                status.avgFillPrice,
                order.orderId,
            )

        if not any(leg.strat_on_trade_exec for leg in self.objs_list):
            self.finish_strategy()


    def _finalize_results(self):
        """Print filled-leg details, the final spread, and net open units."""
        print("\nTRADE PACKAGE FINISHED")
        print("----------------------")

        final_spread = 0
        net_units = 0
        for side, cash_flow_sign in (("buy", -1), ("sell", 1)):
            obj = getattr(self, f"{side}_obj")
            trade = obj.active_trade
            status = trade.orderStatus

            filled_fis = status.filled
            filled_units = filled_fis * obj.scalar_size_units_per_FI * -cash_flow_sign
            net_units += filled_units

            avg_price = status.avgFillPrice
            avg_cf = avg_price * cash_flow_sign
            total_cf = avg_cf - obj.comm_maker_amount + obj.div_adj_cf
            unit_cf = total_cf * obj.scalar_size_FIs_per_unit
            final_spread += unit_cf

            print()
            print(
                obj.my_fi_name,
                obj.buy_or_sell,
                ", filled_FIs:", filled_fis,
                ", filled_units:", f"{filled_units:.2f}",
                ", avg_FI_price:", f"{avg_price:.3f}",
                ", unit_cf:", f"{unit_cf:.3f}"
            )
            print()

        print(
            "Final spread: ", f"{final_spread:.3f}",
            ", Net open units: ", f"{net_units:.2f}", "\n",
        )

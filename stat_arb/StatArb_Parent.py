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

    def __init__(self, group_or_pairs, bo_obj_or_objs_list):
        self.g_or_p = group_or_pairs.lower()

        if self.g_or_p == "group":
            self.bo_obj = bo_obj_or_objs_list
            objs_list = self.bo_obj.objs_list
        else:
            objs_list = bo_obj_or_objs_list

        super().__init__(objs_list)
        self.prepare_on_mkt_data_change()

        for obj in self.objs_list:
            obj.profit_margin = float(obj.profit_margin)
            obj.rest_of_objs_list = [other for other in objs_list if other is not obj]

            for side in ("buy", "sell"):
                setattr(obj, f"active_{side}_trade", None)
                setattr(obj, f"active_{side}_order_input", None)
                setattr(obj, f"active_{side}_order_price", None)

            if self.g_or_p == "pair":
                obj.buy_or_sell = obj.buy_or_sell.upper()
                is_buy = obj.buy_or_sell == "BUY"
                obj.input_price_attr = "cf_unit_lift_ask" if is_buy else "cf_unit_hit_bid"
                obj.input_comm_attr = f"{obj.input_price_attr}_comm"
                obj.div_adj_cf = -obj.div_adj if is_buy else obj.div_adj
                obj.div_adj_unit_cf = obj.div_adj_cf * obj.scalar_size_FIs_per_unit

    def _placed_order_admin(self, obj, trade, input_amt):
        """Record a placed order and optionally print its details."""
        order = trade.order
        side = order.action.lower()

        setattr(obj, f"active_{side}_trade", trade)
        setattr(obj, f"active_{side}_order_input", input_amt)
        setattr(obj, f"active_{side}_order_price", order.lmtPrice)

        if self.need_to_print_active_orders:
            self.print_orders(
                "active",
                side,
                order.totalQuantity,
                obj.my_fi_name,
                order.lmtPrice,
                order.orderId,
            )

    def _finished_order_admin(self, obj, trade):
        """Record a filled leg and finish when no legs need execution handling."""
        order = trade.order
        status = trade.orderStatus
        side = order.action.lower()

        if side == "buy":
            self.buy_obj = obj
        else:
            self.sell_obj = obj

        setattr(obj, f"active_{side}_trade", trade)

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
            trade = getattr(obj, f"active_{side}_trade")
            status = trade.orderStatus

            print()
            print(trade)
            print()

            filled_fis = status.filled
            avg_price = status.avgFillPrice
            comm_cf = -obj.comm_maker_amount * filled_fis
            filled_units = filled_fis * obj.scalar_size_units_per_FI * -cash_flow_sign
            gross_cf = filled_fis * avg_price * cash_flow_sign + comm_cf
            avg_unit_price = gross_cf / filled_units

            final_spread += avg_unit_price
            net_units += filled_units

            print()
            print(
                obj.my_fi_name,
                obj.buy_or_sell,
                ", filled_FIs:", filled_fis,
                ", avg_FI_price:", f"{avg_price:.2f}",
                ", estimated commission:", f"{comm_cf:.2f}",
                ", filled_units:", f"{filled_units:.2f}",
                ", avg_unit_price:", f"{abs(avg_unit_price):.2f}",
            )
            print()

        print(
            "Final spread: ", f"{final_spread:.2f}",
            ", Net open units: ", f"{net_units:.2f}", "\n",
        )


from strategies.Strategy_Parent               import Strategy_Parent
from stat_arb.StatArb_OnClosingPrice  import StatArb_OnClosingPrice
from stat_arb.StatArb_OnMktDataChange import StatArb_OnMktDataChange
from stat_arb.StatArb_OnTradeExec     import StatArb_OnTradeExec


class StatArb_Parent(StatArb_OnClosingPrice,
                        StatArb_OnMktDataChange,
                        StatArb_OnTradeExec,
                        Strategy_Parent):
    
    def __init__(self, group_or_pairs, bo_obj_or_objs_list):
        self.g_or_p = group_or_pairs.lower()
        
        if self.g_or_p == "group":
            self.bo_obj = bo_obj_or_objs_list
            objs_list = self.bo_obj.objs_list
        else: # pairs
            objs_list = bo_obj_or_objs_list

        super().__init__(objs_list)  # this calls Strategy_Parent.__init__() 

        # create self attributes
        self.prepare_on_mkt_data_change()

        # attach attributes to objs
        for obj in self.objs_list:
            obj.profit_margin = float(obj.profit_margin)

            obj.rest_of_objs_list = [o for o in objs_list if o is not obj]

            obj.active_buy_trade = None
            obj.active_sell_trade = None

            obj.active_buy_order_input = None
            obj.active_sell_order_input = None

            obj.active_buy_order_price = None
            obj.active_sell_order_price = None

            if self.g_or_p == "pair":
                obj.buy_or_sell = obj.buy_or_sell.upper()
                if obj.buy_or_sell == 'BUY':
                    obj.input_price_attr = 'cf_unit_lift_ask'
                    obj.input_comm_attr = "cf_unit_lift_ask_comm"
                    obj.div_adj_cf = -obj.div_adj
                else:
                    obj.input_price_attr = 'cf_unit_hit_bid'
                    obj.input_comm_attr = "cf_unit_hit_bid_comm"
                    obj.div_adj_cf = obj.div_adj
                obj.div_adj_unit_cf = obj.div_adj_cf * obj.scalar_size_FIs_per_unit
 
     
    def _placed_order_admin(self, obj, trade, input_amt):
        trade_order = trade.order

        buy_sell = trade_order.action.lower()
        order_price = trade_order.lmtPrice
        order_id = trade_order.orderId

        setattr(obj, f"active_{buy_sell}_trade", trade)
        setattr(obj, f"active_{buy_sell}_order_input", input_amt)
        setattr(obj, f"active_{buy_sell}_order_price", order_price)

        if self.need_to_print_active_orders:
            self.print_orders("active",
                              buy_sell, 
                              trade_order.totalQuantity, 
                              obj.my_fi_name,
                              order_price, 
                              order_id)


    def _finished_order_admin(self, obj, trade):
        trade_order = trade.order
        trade_orderStatus = trade.orderStatus

        buy_sell = trade_order.action

        if buy_sell.lower() == "buy":
            self.buy_obj = obj
        else:
            self.sell_obj = obj

        setattr(obj, f"active_{buy_sell.lower()}_trade", trade)

        if self.need_to_print_finished_orders:
            self.print_orders("finished", 
                              trade_order.action, 
                              trade_orderStatus.filled, 
                              obj.my_fi_name, 
                              trade_orderStatus.avgFillPrice, 
                              trade_order.orderId)

        if not any(obj.strat_on_trade_exec for obj in self.objs_list):
            self.finish_strategy()

            
    def _finalize_results(self):
        print("\nTRADE PACKAGE FINISHED")
        print("----------------------")

        final_spread = 0
        net_units = 0
        for buy_sell in ['BUY', 'SELL']:
            buy_sell_lower = buy_sell.lower()
            buy_sell_scalar = 1 if buy_sell == 'SELL' else -1

            obj = getattr(self, f"{buy_sell_lower}_obj")
            trade = getattr(obj, f"active_{buy_sell_lower}_trade")
            trade_order_status = trade.orderStatus

            print()
            print(trade)
            print()

            filled_FIs = trade_order_status.filled
            avg_price = trade_order_status.avgFillPrice

            comm_cf = -obj.comm_maker_amount * filled_FIs

            filled_units = filled_FIs * obj.scalar_size_units_per_FI * -buy_sell_scalar

            gross_cf = filled_FIs * avg_price * buy_sell_scalar + comm_cf
            avg_unit_price = (gross_cf / filled_units)

            final_spread += avg_unit_price
            net_units += filled_units

            print()
            print(
                obj.my_fi_name,
                obj.buy_or_sell,
                ", filled_FIs:",       filled_FIs,
                ", avg_FI_price:",     f'{avg_price:.2f}',
                ", estimated commission:", f'{comm_cf:.2f}',
                ", filled_units:",     f'{filled_units:.2f}',
                ", avg_unit_price:",   f'{abs(avg_unit_price):.2f}'
            )
            print()

        print('Final spread: ', f'{final_spread:.2f}', ', Net open units: ', f'{net_units:.2f}', '\n')

        

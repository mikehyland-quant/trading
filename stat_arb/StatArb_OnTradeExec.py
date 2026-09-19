
# handles the on trade execution event and updates the trades accordingly
class StatArb_OnTradeExec():
  
    def on_trade_exec(self, filled_obj, filled_trade):  
        if filled_trade.orderStatus.filled == 0:   # this will be the case many times
            return 

        if filled_obj.strat_on_mkt_data_change:  # then this is first fill
            self._on_first_fill(filled_obj, filled_trade)
        else:  # this is second fill
            self._on_second_fill(filled_obj, filled_trade)


    def _on_first_fill(self, filled_obj, filled_trade):
        filled_buy_sell_lower = filled_trade.order.action.lower()
        if filled_buy_sell_lower == 'buy':
            hit_lift = "hit_bid"
        else:
            hit_lift = 'lift_ask'

        if self.g_or_p == "group":
            objs_list = getattr(self.bo_obj, f"strat_{hit_lift}_ranked_objs_list")
        else: # pairs
            objs_list = filled_obj.rest_of_objs_list

        self.send_follow_up_orders(objs_list, filled_obj, filled_trade, filled_buy_sell_lower) # see specialty code

        for obj in filled_obj.rest_of_objs_list:
            order_to_cancel = getattr(obj, f"active_{filled_buy_sell_lower}_trade")
            self.cancel_order(obj, order_to_cancel)
            obj.strat_on_mkt_data_change = False

        filled_obj.strat_on_mkt_data_change = False
        self._finished_order_admin(filled_obj, filled_trade) 

 
    def _on_second_fill(self, filled_obj, filled_trade):
        filled_buy_sell_lower = filled_trade.order.action.lower()
         
        for obj in self.objs_list:
            order_to_cancel = getattr(obj, f"active_{filled_buy_sell_lower}_trade")
            self.cancel_order(obj, order_to_cancel)
            obj.strat_on_trade_exec = False

        self._finished_order_admin(filled_obj, filled_trade) 

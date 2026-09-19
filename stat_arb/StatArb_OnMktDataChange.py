
import asyncio

# handles the on market data change event and updates the trades accordingly
class StatArb_OnMktDataChange():

# ============================================================
# THIS CODE ALLOWS FOR ONLY THE MOST RECENT UPDATES TO BE PROCESSED
# ============================================================
 
    def prepare_on_mkt_data_change(self):
        self.pending_mkt_data_objs = set()
 
        self.need_to_update        = False
        self.is_update_in_progress = False

        self.update_task           = None


    def on_mkt_data_change(self, obj):
        self.pending_mkt_data_objs.add(obj)
        self.need_to_update = True

        if self.is_update_in_progress:
            return

        self.is_update_in_progress = True
        
        self.update_task = asyncio.create_task(self._update_trade_worker())


    async def _update_trade_worker(self):
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

# ============================================================
# THIS CODE ACTUALLY UPDATES THE OUTPUT PRICES BASED ON THE NEW INPUT PRICES
# ============================================================

    async def _update_trade(self, updated_obj):
        if not updated_obj.is_mkt_data_valid():
            return

        if self.g_or_p == "group":
            bo_obj = self.bo_obj
        
            for output_obj in self.objs_list:
                x = await self._update_trade_details(output_obj, "buy", bo_obj.strat_hit_bid_ranked_amts_list[0])
                x = await self._update_trade_details(output_obj, "sell", bo_obj.strat_lift_ask_ranked_amts_list[0])

        else: # g_or_p == "pairs"
            trade_cf = getattr(updated_obj, updated_obj.input_price_attr)
            comm_cf = getattr(updated_obj, updated_obj.input_comm_attr)
            new_input_amt = trade_cf + comm_cf + updated_obj.div_adj_unit_cf
            # print(updated_obj.my_fi_name, trade_cf, comm_cf, new_input_amt)
            for output_obj in updated_obj.rest_of_objs_list:
                x = await self._update_trade_details(output_obj, output_obj.buy_or_sell.lower(), new_input_amt)


    async def _update_trade_details(self, output_obj, buy_sell, new_input_amt):
        if not output_obj.is_mkt_data_valid():
            return
        
        active_order_input = getattr(output_obj, f"active_{buy_sell}_order_input")           
        if abs(active_order_input - new_input_amt) < 1e-9:
            return

        active_order_price = getattr(output_obj, f"active_{buy_sell}_order_price")

        margin = getattr(output_obj, "profit_margin") # f"{buy_sell}_profit_margin")   
        profitable_unit_cf = margin - new_input_amt  

#####
#       adjust profitable_unit_cf for div_adj_unit_cf
#####

        [new_order_price, x_comm] = output_obj.decompose_unit_cf(profitable_unit_cf, 'taker')
        new_order_price = output_obj.round_price_to_tick(abs(new_order_price), buy_sell.upper())

        # print(new_input_amt, margin, profitable_unit_cf, new_order_price)

        if abs(active_order_price - new_order_price) < 1e-9:
            return
        
        active_trade = getattr(output_obj, f"active_{buy_sell}_trade")
        new_trade = self.update_limit_order(obj=output_obj, 
                                            trade=active_trade, 
                                            price=new_order_price)
        
        if new_trade is not None:
            self._placed_order_admin(output_obj, new_trade, new_input_amt)


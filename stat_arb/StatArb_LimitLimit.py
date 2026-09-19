
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from stat_arb.StatArb_Parent import StatArb_Parent

class StatArb_LimitLimit(StatArb_Parent):

    def __init__(self, group_or_pair, bo_obj_or_objs_list):
        super().__init__(group_or_pair, bo_obj_or_objs_list) 
 
    def send_follow_up_orders(self, objs_list, filled_obj, filled_trade, filled_buy_sell_lower):  
        if filled_buy_sell_lower == 'buy':
            unfilled_buy_sell_upper = 'SELL'
            unfilled_buy_sell_lower = 'sell'
            buy_sell_scalar = -1
        else:
            unfilled_buy_sell_upper = 'BUY'
            unfilled_buy_sell_lower = 'buy'
            buy_sell_scalar = 1

        filled_trade_order_status = filled_trade.orderStatus

        avg_filled_price = filled_trade_order_status.avgFillPrice



#####        
        commission_cf = -0.0055 # getattr(filled_obj, filled_obj.input_comm_attr))

        avg_filled_cf = buy_sell_scalar * avg_filled_price + filled_obj.div_adj_cf + commission_cf
#####


        new_input_amt = avg_filled_cf * filled_obj.scalar_size_FIs_per_unit

        profitable_unit_cf = filled_obj.profit_margin - new_input_amt

        for output_obj in objs_list:
#####
#           adjust profitable_unit_cf for div_adj_unit_cf
#####

            [new_order_price, x_comm] = output_obj.decompose_unit_cf(profitable_unit_cf, 'taker')
            new_order_price = output_obj.round_price_to_tick(abs(new_order_price), unfilled_buy_sell_upper)

            active_trade = getattr(output_obj, f"active_{unfilled_buy_sell_lower}_trade")
            new_trade = self.update_limit_order(obj=output_obj, 
                                                trade=active_trade, 
                                                price=new_order_price)
              
            if new_trade is not None:
                self._placed_order_admin(output_obj, new_trade, new_input_amt)

 
    '''
    #  if initial trades are all-or-none then no need for balancing orders
    '''

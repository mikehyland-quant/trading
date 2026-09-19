#!/usr/bin/env python
# coding: utf-8

# In[ ]:

'''
This class assigns a number of attributes to each object.  The attribute names are organized as follows:
    first  - cf / comm / price / size
    second - mkt / raw / unit
    third  - ask / bid / hit_bid / join_ask / join_bid / lift_ask 

For scalars, the first is always "scalar".  Then price / size.  Then raw_to_mkt / mkt_to_unit.
'''

 
import numpy as np
import pandas as pd
import time

class MktData:
    
    def __init__(self):
        self.subscribers = []
        self.avg_daily_volume = 0

        self.scalar_price_raw_to_screen  = 1  # overwritten in platform if necessary (this is the IBKR priceMagnifier)
        self.scalar_size_raw_to_screen   = 1  # overwritten in platform if necessary

        self.scalar_size_FIs_per_unit    = 1  # overwritten in product if necessary (see equity and option)
        self.scalar_size_FIs_per_order   = 1  # overwritten in platform if necessary (this is the IBKR multiplier)

        # the scalars below are all recalced as part of obj.complete_obj()
        self.scalar_size_units_per_FI    = 1
        self.scalar_size_orders_per_FI   = 1 
        self.scalar_size_orders_per_unit = 1
        self.scalar_size_units_per_order = 1

        # the next line is calculated as part of complete object
        # self.scalar_units_per_screen  = 1 / self.scalar_screens_per_unit

        self.actively_updating_mkt_data = False
        self.need_to_save_closing_price = True
        
        self.price_raw_close    = np.nan
        self.price_screen_close = np.nan
        self.price_unit_close   = np.nan
               
        for bid_ask in ['bid', 'ask']:
            setattr(self, 'price_raw_'    + bid_ask, np.nan)     
            setattr(self, 'price_screen_' + bid_ask, np.nan)       
            setattr(self, 'price_unit_'   + bid_ask, np.nan)    

            setattr(self, 'size_raw_'     + bid_ask, np.nan) 
            setattr(self, 'size_screen_'  + bid_ask, np.nan)               
            setattr(self, 'size_unit_'    + bid_ask, np.nan)    

        for bid_ask in ['hit_bid', 'join_bid', 'join_ask', 'lift_ask']:   
            setattr(self, 'cf_order_'    + bid_ask, np.nan)        
            setattr(self, 'cf_unit_'     + bid_ask, np.nan)  
            
            setattr(self, 'cf_order_'    + bid_ask + "_comm", np.nan)        
            setattr(self, 'cf_unit_'     + bid_ask + "_comm", np.nan)  
        
             
    @staticmethod
    def _safe_float(x, default=np.nan):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default
        

    def is_mkt_data_valid(self):
        bid = self.price_screen_bid
        ask = self.price_screen_ask
        b_sz = self.size_screen_bid
        a_sz = self.size_screen_ask
        return (pd.notna(bid) and pd.notna(ask) and pd.notna(b_sz) and pd.notna(a_sz) and bid < ask)
       
 
    def on_mkt_data_change(self, bid_price=np.nan, ask_price=np.nan, bid_size=np.nan, ask_size=np.nan): 
      
        changed = self.update_mkt_data(bid_price=bid_price, ask_price=ask_price, bid_size=bid_size, ask_size=ask_size) 
            #self.update_mkt_data() is overwritten in strategy classes to update additional attributes when necessary

        if not changed:
             return 
               
        for subscriber in self.subscribers:
            subscriber.update_subscriber_data(self)

        strategy = getattr(self, "strategy", None)
        if strategy is not None and getattr(self, "strat_on_mkt_data_change", False):
            strategy.on_mkt_data_change(self)


    def update_mkt_data(self, bid_price=np.nan, ask_price=np.nan, bid_size=np.nan, ask_size=np.nan):        
        changed = False
        ts = np.nan
    
        bid_price = self._safe_float(bid_price, default=np.nan)
        ask_price = self._safe_float(ask_price, default=np.nan)
        bid_size  = self._safe_float(bid_size,  default=np.nan)
        ask_size  = self._safe_float(ask_size,  default=np.nan)

        # print(bid_price, ask_price, bid_size, ask_size)

        if pd.notna(bid_price) and bid_price != self.price_raw_bid:
            changed = True
            
            if pd.isna(ts):
                ts = time.time_ns()
            self.ts_price_bid = ts
                    
            self.price_raw_bid          =  bid_price
            self.price_screen_bid       =  self.price_raw_bid    * self.scalar_price_raw_to_screen    
            self.price_order_bid        =  self.price_screen_bid * self.scalar_size_FIs_per_order 
            self.price_unit_bid         =  self.price_order_bid  * self.scalar_size_orders_per_unit 

            self.cf_order_join_bid      = -self.price_order_bid 
            self.cf_order_hit_bid       =  self.price_order_bid 

            self.cf_unit_join_bid       = -self.price_unit_bid 
            self.cf_unit_hit_bid        =  self.price_unit_bid 

            self.cf_order_join_bid_comm = -self.calc_comm(self.price_screen_bid, 'maker')
            self.cf_order_hit_bid_comm  = -self.calc_comm(self.price_screen_bid, 'taker')
    
            self.cf_unit_join_bid_comm  =  self.comm_order_join_bid * self.scalar_size_orders_per_unit
            self.cf_unit_hit_bid_comm   =  self.comm_order_hit_bid  * self.scalar_size_orders_per_unit
    
        if pd.notna(ask_price) and ask_price != self.price_raw_ask:
            changed = True
            
            if pd.isna(ts):
                ts = time.time_ns()
            self.ts_price_ask = ts
                     
            self.price_raw_ask          =  ask_price
            self.price_screen_ask       =  self.price_raw_ask    * self.scalar_price_raw_to_screen    
            self.price_order_ask        =  self.price_screen_ask * self.scalar_size_FIs_per_order
            self.price_unit_ask         =  self.price_order_ask  * self.scalar_size_orders_per_unit

            self.cf_order_join_ask      =  self.price_order_ask 
            self.cf_order_lift_ask      = -self.price_order_ask 

            self.cf_unit_join_ask       =  self.price_unit_ask 
            self.cf_unit_lift_ask       = -self.price_unit_ask 

            self.cf_order_join_ask_comm = -self.calc_comm(self.price_screen_ask, 'maker')
            self.cf_order_lift_ask_comm = -self.calc_comm(self.price_screen_ask, 'taker')
    
            self.cf_unit_join_ask_comm  =  self.comm_order_join_ask * self.scalar_size_orders_per_unit
            self.cf_unit_lift_ask_comm  =  self.comm_order_lift_ask * self.scalar_size_orders_per_unit

            # self.cf_plus_comm_unit_join_ask = self.cf_unit_join_ask - self.comm_unit_join_ask
            # self.cf_plus_comm_unit_lift_ask = self.cf_unit_lift_ask - self.comm_unit_lift_ask


        if pd.notna(bid_size) and bid_size != self.size_raw_bid:
            changed = True
             
            if pd.isna(ts):
                ts = time.time_ns()
            self.ts_size_bid = ts
                    
            self.size_raw_bid    =  bid_size    
            self.size_screen_bid =  self.size_raw_bid    * self.scalar_size_raw_to_screen
            self.size_unit_bid   =  self.size_screen_bid * self.scalar_size_units_per_order

        if pd.notna(ask_size) and ask_size != self.size_raw_ask:
            changed = True
            
            if pd.isna(ts):
                ts = time.time_ns()
            self.ts_size_ask = ts
            
            self.size_raw_ask    =  ask_size    
            self.size_screen_ask =  self.size_raw_ask    * self.scalar_size_raw_to_screen
            self.size_unit_ask   =  self.size_screen_ask * self.scalar_size_units_per_order
    
        # print(self.price_unit_ask, self.price_unit_bid, self.size_unit_bid, self.size_unit_ask)

        return changed
        
     
    def calc_comm(self, price, maker_taker): 
        type_ = self.comm_type
        amount = getattr(self, 'comm_' + maker_taker + '_amount')

        if type_ == 'flat_amt':
            return float(amount)
        elif type_ == 'flat_pct':
            return price * amount  
        elif type_ == 'flat_pct_with_min':
            initial_estimate = price * amount
            return max(initial_estimate, self.comm_misc_amount)
        else:
            return 0 
        

    def on_close_update(self, close_price=np.nan):
        close_price = self._safe_float(close_price, default=np.nan)
        
        if close_price is not None and not np.isnan(close_price):
            self.price_raw_close    = close_price 

            self.price_screen_close = self.price_raw_close    * self.scalar_price_raw_to_screen
            self.price_unit_close   = self.price_screen_close * self.scalar_size_FIs_per_unit
            
            strategy = getattr(self, "strategy", np.nan)
            if getattr(self, "strat_on_closing_price", False):  
                strategy.on_closing_price(self)

            self.actively_updating_mkt_data = True
            self.need_to_save_closing_price = False


    def decompose_unit_cf(self, unit_cf, maker_taker="0"):
        non_unit_cf = unit_cf / self.scalar_size_FIs_per_unit

        if maker_taker == "0":
            comm = 0
            cf = non_unit_cf
        else:
            cf, comm = self.decompose_non_unit_cf(non_unit_cf, maker_taker)
   
        return [cf, comm]


    def decompose_non_unit_cf(self, non_unit_cf, maker_taker):
    # total_cf must include a sign

        type_ = self.comm_type
        amount = float(getattr(self, 'comm_' + maker_taker + '_amount'))

        if type_ == 'flat_amt':
            comm = -amount
            cf = non_unit_cf - comm
        elif type_ == 'flat_pct':
            if non_unit_cf > 0:
                amount = -amount
            cf = non_unit_cf / (1 + amount)
            comm = non_unit_cf - cf
        elif type_ == 'flat_pct_with_min':
            if non_unit_cf > 0:
                amount = -amount
            cf = non_unit_cf / (1 + amount)
            comm = non_unit_cf - cf
            min_comm = float(self.comm_misc_amount)
            if abs(comm) < min_comm:
                comm = -min_comm
                cf = non_unit_cf - comm
        else:
            cf = non_unit_cf
            comm = 0
        return cf, comm
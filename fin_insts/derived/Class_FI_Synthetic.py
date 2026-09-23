
from fin_insts.derived.Class_FI_Subscriber import Subscriber
from other.Graph_Theory import find_all_node_permutations, create_list_of_edges, prepend_edge

import numpy as np


'''
NEED TIMESTAMP LOGIC EVENTUALLY
def _aggregate_timestamp(self, dict_name, key):         # reconsider whether you want time of oldest or newest quote
    vals = [getattr(obj, dict_name, {}).get(key) for obj, x in self.legs_list]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None
'''


class Synthetic(Subscriber):

    consensus_attr_list = [
        'my_pf_name',
        'numerator_currency',
        'denominator_currency',
        'quote_currency',
        'settlement_currency',
        'pf_prod_type',
                        ]

    def __init__(self, my_name, instruments_list):       
        super().__init__()
        
        self.legs_list = instruments_list # list of tuples: (obj, +/- amount)
        self.objs_list = [obj for obj, x in instruments_list]

        for obj in self.objs_list:
            obj.subscribers.append(self)
        
        self.my_prod_type = 'synthetic'
        self.my_fi_name   = 'syn ' + my_name 
        
        for attr in self.consensus_attr_list:
            setattr(self, attr, self._consensus_attr(attr))


    def _consensus_attr(self, attr_name):
        values = {getattr(obj, attr_name, None) for obj in self.objs_list}
        return values.pop() if len(values) == 1 else "multi"


    def _set_invalid_prices(self):
        self.price_unit_bid = np.nan
        self.price_unit_ask = np.nan
    
        self.cf_unit_join_bid = np.nan
        self.cf_unit_hit_bid  = np.nan
    
        self.cf_unit_join_ask = np.nan
        self.cf_unit_lift_ask = np.nan
    
        self.comm_unit_join_bid = np.nan
        self.comm_unit_hit_bid  = np.nan
    
        self.comm_unit_join_ask = np.nan
        self.comm_unit_lift_ask = np.nan

    
    def update_subscriber_data(self, obj):
        self.update_syn()
        for subscriber in self.subscribers:
            subscriber.update_subscriber_data()

    
    def update_syn(self):
        self.update_prices()
        self.update_sizes()
    
    
    def update_prices(self):   
        self.price_unit_bid     = 0
        self.price_unit_ask     = 0
        
        self.cf_unit_join_bid   = 0
        self.cf_unit_hit_bid    = 0
            
        self.cf_unit_join_ask   = 0
        self.cf_unit_lift_ask   = 0

        self.comm_unit_join_bid = 0
        self.comm_unit_hit_bid  = 0

        self.comm_unit_join_ask = 0
        self.comm_unit_lift_ask = 0
               
        for obj, scalar in self.legs_list:  
            if not obj.is_mkt_data_valid():            
                self._set_invalid_prices()
                return
                            
            if scalar == 1:                
                self.price_unit_bid     += obj.price_unit_bid
                self.price_unit_ask     += obj.price_unit_ask
                                     
                self.cf_unit_join_bid   += obj.cf_unit_join_bid
                self.cf_unit_hit_bid    += obj.cf_unit_hit_bid
                                    
                self.cf_unit_join_ask   += obj.cf_unit_join_ask
                self.cf_unit_lift_ask   += obj.cf_unit_lift_ask

                self.comm_unit_join_bid += obj.cf_unit_join_bid_comm
                self.comm_unit_hit_bid  += obj.cf_unit_hit_bid_comm
                           
                self.comm_unit_join_ask += obj.cf_unit_join_ask_comm
                self.comm_unit_lift_ask += obj.cf_unit_lift_ask_comm
                                    
            elif scalar == -1:
                self.price_unit_bid     += -obj.price_unit_ask
                self.price_unit_ask     += -obj.price_unit_bid
                
                self.cf_unit_join_bid   += obj.cf_unit_join_ask
                self.cf_unit_hit_bid    += obj.cf_unit_lift_ask
                    
                self.cf_unit_join_ask   += obj.cf_unit_join_bid
                self.cf_unit_lift_ask   += obj.cf_unit_hit_bid

                self.comm_unit_join_bid += obj.cf_unit_join_ask_comm
                self.comm_unit_hit_bid  += obj.cf_unit_lift_ask_comm
                           
                self.comm_unit_join_ask += obj.cf_unit_join_bid_comm
                self.comm_unit_lift_ask += obj.cf_unit_hit_bid_comm
            

    def update_sizes(self):
        obj, scalar = self.legs_list[0]
        
        if not obj.is_mkt_data_valid():
            self.size_unit_bid = 0
            self.size_unit_ask = 0
            return
        
        if scalar == 1:
            self.size_unit_bid = obj.size_unit_bid
            self.size_unit_ask = obj.size_unit_ask

        elif scalar == -1:
            self.size_unit_bid = obj.size_unit_ask
            self.size_unit_ask = obj.size_unit_bid

        for obj, scalar in self.legs_list[1:]:
            if not obj.is_mkt_data_valid():
                self.size_unit_bid = 0
                self.size_unit_ask = 0
                return

            if scalar == 1:
                self.size_unit_bid = min(obj.size_unit_bid, self.size_unit_bid)
                self.size_unit_ask = min(obj.size_unit_ask, self.size_unit_ask)

            elif scalar == -1:
                self.size_unit_bid = min(obj.size_unit_ask, self.size_unit_bid)
                self.size_unit_ask = min(obj.size_unit_bid, self.size_unit_ask)


    @staticmethod
    def make_syn_futures_list(futures_list, fut_spd_list):
        expiry_dict = {obj.date_expiry : obj.my_fi_name for obj in futures_list}
        
        # all permutations of 2 or more unique expiration dates
        nodes_perms_list = find_all_node_permutations(expiry_dict.keys(), min_num=2)

        # build valid synthetic paths
        syn_obj_list = []
        for node_list in nodes_perms_list:
            edge_list = create_list_of_edges(node_list, fut_spd_list,
                                             'date_expiry_near', 'date_expiry_far')

            edge_list = prepend_edge(futures_list, 'date_expiry', 
                                     edge_list, 'date_expiry_near', 'date_expiry_far')
            
            if edge_list is not None:   
                name = "/".join(expiry_dict[date] for date in node_list)  
                syn_obj = Synthetic(name, edge_list)
                syn_obj.final_date = node_list[-1]
                syn_obj.final_contract = expiry_dict[syn_obj.final_date]
                syn_obj_list.append(syn_obj)

        syn_obj_list = sorted(syn_obj_list, key=lambda obj: obj.final_date)
        
        return syn_obj_list
            


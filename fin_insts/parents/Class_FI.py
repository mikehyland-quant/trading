'''
creation process for a financial instrument
    1. external data supplied - usually from a database style spreadsheet
    2. make_single_leg_fin_insts(df) usually instantiates objects
    3. instantiation includes MktData - parent to FinancialInstrument below
    4. data related to the object is retrieved from venue via complete_objects and get_product_info in Class_WS_FeedBase
    5. complete_obj in Class_WS_FeedBase applies venue data to object
    6. complete_obj in Class_WS_FeedBase leads to the objects own complete_obj function below
    7. obj.complete_obj calls Dates
'''
 

from fin_insts.parents.Class_FI_Dates import Dates
from fin_insts.parents.Class_FI_MktData import MktData
# from datetime import date
 

class FinancialInstrument(MktData):
    """
    Parent class for all financial instruments.
    """
 
    def __init__(self, my_row):
        super().__init__()

        #----- Constants assigned at object creation ------
        #self.subscribers = [] -  assigned in MktData
        self.my_row = my_row
        
        self.my_prod_type = self.my_row.my_prod_type
        self.my_fi_name   = self.my_row.my_fi_name
        self.my_pf_name   = self.my_row.my_pf_name
        self.pf_locator   = self.my_row.pf_locator

        #----- Assigned at object creation but potentially overwritten later at product level -----
        self.comm_type         = self.my_row.comm_type
        self.comm_maker_amount = self._safe_float(self.my_row.comm_maker)
        self.comm_taker_amount = self._safe_float(self.my_row.comm_taker)
        self.comm_misc_amount  = self._safe_float(self.my_row.comm_misc)
        
        #----- Assigned by IBKR if appropriate -----
        #self.my_ibkr_id =         # need this for BAGs
        #self.ibkr_contract = 
        #self.ibkr_details = 
     
        #----- Retrieved from venue if available ------
        #self.fi_row = 
        
        #self.pf_symbol = 
        #self.pf_number = 
        #self.pf_prod_type = 
        
        #self.numerator_currency =        
        #self.denominator_currency = 
        #self.quote_currency = 
        #self.settlement_currency = 
                   
        #----- Other  -----
        #dates - assigned as part of complete_obj below - see Dates

    def complete_obj(self):
        Dates.calc_and_attach(self)

        self.scalar_size_orders_per_unit = self.scalar_size_FIs_per_unit / self.scalar_size_FIs_per_order

        self.scalar_size_units_per_FI    = 1 / self.scalar_size_FIs_per_unit
        self.scalar_size_orders_per_FI   = 1 / self.scalar_size_FIs_per_order
        self.scalar_size_units_per_order = 1 / self.scalar_size_orders_per_unit
        
        
        
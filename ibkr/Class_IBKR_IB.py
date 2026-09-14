
# import asyncio

import asyncio
import math
import pandas as pd
# import numpy as np

from datetime import datetime
from zoneinfo import ZoneInfo
 
from ib_insync import IB, Contract, ComboLeg, LimitOrder, MarketOrder, Order, obj

from fin_insts.parents.Class_FI_Dates import Dates

class IBKR_IB:
    
    def __init__(self, host='127.0.0.1', port=7496):
        self.host = host
        self.port = port
        self.clientId = int(datetime.now().strftime("%H%M%S"))

        self.ib = IB()
        self.ticker_dict = {}  # created in stream_contract
        self.obj_by_order_handler = {}  # created in place_limit_order

        # note that IBKR sometimes mis-spells "Cancelled" as "Canceled"
        self.ACTIVE_STATUSES    = {"ApiPending", "PendingSubmit", "PreSubmitted", "Submitted"}

        self.SAFE_TO_MODIFY     = {"PreSubmitted", "Submitted"}

        self.DONE_STATUSES      = {"ApiCancelled", "Cancelled", "Canceled", "Filled", "Inactive", }

        self.NOT_SAFE_TO_MODIFY = {"ApiPending", "PendingSubmit", "PendingCancel", "ApiCancelled",
                                   "Cancelled", "Canceled", "Filled", "Inactive"}


    async def contract_by_conId(self, conid):
        contract = Contract(conId=int(conid))
        await self.ib.qualifyContractsAsync(contract)
        return contract
    
    
    async def create_simple_contract(self, obj):
        #print(obj.my_fi_name)
        obj.ibkr_contract = await self.contract_by_conId(int(obj.pf_locator))
        obj.ibkr_details = (await self.ib.reqContractDetailsAsync(obj.ibkr_contract))[0]

 
    async def create_bag_contract(self, obj1, action1, size1, obj2, action2, size2):
        # for futures spreads obj1 should be far obj and obj2 should be near obj
        # for options combos obj1 should be the call and obj2 should be the put

        leg1 = ComboLeg()
        leg1.conId = int(obj1.ibkr_contract.conId)
        leg1.ratio = size1
        leg1.action = action1.upper()
        leg1.exchange = obj1.ibkr_contract.exchange

        leg2 = ComboLeg()
        leg2.conId = int(obj2.ibkr_contract.conId)
        leg2.ratio = size2
        leg2.action = action2.upper()
        leg2.exchange = obj2.ibkr_contract.exchange

        bag = Contract()
        bag.symbol = obj1.ibkr_contract.symbol
        bag.secType = 'BAG'
        bag.currency = obj1.ibkr_contract.currency
        bag.exchange = 'SMART' # or obj2.ibkr_contract.exchange
        bag.comboLegs = [leg1, leg2]

        return bag         
        # no need to return ibkr_details as this doesn't exist for BAG contracts

 
    @classmethod
    async def complete_obj(cls, obj):
        obj.pf_symbol    = obj.ibkr_contract.localSymbol
        obj.pf_number    = obj.ibkr_contract.conId
        obj.pf_prod_type = obj.ibkr_contract.secType

        obj.numerator_currency   = obj.my_row.top_currency
        obj.denominator_currency = obj.my_row.base_currency
        obj.quote_currency       = None
        obj.settlement_currency  = None

        obj.min_tick       = obj._safe_float(getattr(obj.ibkr_details, 'minTick'), 1.0)
        obj.min_size       = obj._safe_float(getattr(obj.ibkr_details, 'minSize'), 1.0)
        obj.size_increment = obj._safe_float(getattr(obj.ibkr_details, 'sizeIncrement'), 1.0)

        obj.scalar_price_raw_to_screen = obj._safe_float(getattr(obj.ibkr_details,  'priceMagnifier'), 1.0)
        obj.scalar_size_FIs_per_order  = obj._safe_float(getattr(obj.ibkr_contract, 'multiplier')    , 1.0)
        
        if obj.pf_prod_type in ['FUT', "FOP", "OPT"]:
            obj.date_expiry      = Dates.date_from_string(obj.ibkr_details.realExpirationDate)         
            obj.last_trade_time  = Dates.time_from_number(obj.ibkr_details.lastTradeTime, 24)
            obj.tz_exch          = ZoneInfo(obj.ibkr_details.timeZoneId or "US/Central")

            if obj.pf_prod_type in ['FOP', 'OPT']:
                obj.underlying_symbol = obj.ibkr_contract.symbol
                obj.p_or_c            = obj.ibkr_contract.right
                obj.strike_price      = obj.ibkr_contract.strike

        obj.complete_obj()
        

    async def connect(self):
        # print(f"Connecting to IBKR host={self.host}, port={self.port}, clientId={self.clientId}")
        if not self.ib.isConnected():
            await self.ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.clientId
            )
        #print("Next Order ID:", self.ib.client.getReqId())
        return self.ib.isConnected()


    async def start_streams(self, dict_or_list):
        if isinstance(dict_or_list, dict):
            objs = list(dict_or_list.values())
        else:
            objs = dict_or_list

        for obj in objs:
            await self.stream_contract(obj, self.tick_handler)
            

    async def stream_contract(self, obj, handler):
        if obj.ibkr_contract.secType == 'BAG':
            ticker = self.ib.reqMktData(obj.ibkr_contract, "233", False, False)
        else:
            ticker = self.ib.reqMktData(obj.ibkr_contract, "", False, False)       
        
        ticker.updateEvent += handler
        self.ticker_dict[ticker] = obj

        #print(ticker, '\n')
        
        return ticker
          

    def tick_handler(self, ticker):
        '''
        IBKRClient.tick_handler is a synchronous method used as an event callback, 
        which works fine with ib_insync's internal event loop, but worth noting — 
        if you ever add latency-sensitive logic there it needs to stay non-blocking.

        '''
        # print(ticker, '\n')

        obj = self.ticker_dict.get(ticker)
        if obj is None:
            return
        
        # print(obj.actively_updating_mkt_data, obj.need_to_save_closing_price)

        if obj.actively_updating_mkt_data:
            obj.on_mkt_data_change(bid_price=ticker.bid,
                                   ask_price=ticker.ask,
                                   bid_size=ticker.bidSize,
                                   ask_size=ticker.askSize)
        
        elif obj.need_to_save_closing_price:     
            obj.on_close_update(close_price=ticker.close)
           

    def order_handler(self, trade):
        # print(trade, '\n')
        
        obj = self.obj_by_order_handler.get(trade.order)
        if obj is None:
            return

        strategy = getattr(obj, "strategy", None)
        if strategy is not None and getattr(obj, "strat_on_trade_exec", False):
            # asyncio.create_task(strategy.on_trade_exec(obj, trade))
            strategy.on_trade_exec(obj, trade)

                          
    def place_market_order(self, obj=None, size=None, buy_sell=None, tif='DAY'):
        tif = 'Minutes' if obj.pf_prod_type == 'CRYPTO' else 'DAY'
        
        order = MarketOrder(action=buy_sell, totalQuantity=size, tif=tif)
        print(order, '\n')

        trade = self.ib.placeOrder(obj.ibkr_contract, order)
        print(trade, '\n')

        self.obj_by_order_handler[order] = obj
        trade.statusEvent += self.order_handler
        
        return trade
    

    def place_limit_order(self, obj=None, size=None, buy_sell=None, price=None, tif='DAY', all_or_none=False):
        tif = 'Minutes' if obj.pf_prod_type == 'CRYPTO' else 'DAY'

        order = LimitOrder(action=buy_sell, totalQuantity=size, lmtPrice=price, tif=tif, allOrNone=all_or_none)
        print(order, '\n')

        trade = self.ib.placeOrder(obj.ibkr_contract, order)
        print(trade, '\n')

        self.obj_by_order_handler[order] = obj
        trade.statusEvent += self.order_handler

        return trade
 

    def modify_to_market_order(self, obj=None, size=None, buy_sell=None, trade=None):
        if trade is None:
            raise ValueError(f"Trade not found")

        if trade.orderStatus.status not in self.SAFE_TO_MODIFY:
            #raise ValueError(f"Order {trade.order.orderId} is not in a modifiable state")
            return

        trade.order.orderType = "MKT"

        # Important: market orders should not keep a limit price
        trade.order.lmtPrice = None

        # Only overwrite if explicitly provided
        if size is not None:
            trade.order.totalQuantity = size

        if buy_sell is not None:
            trade.order.action = buy_sell
    
        trade = self.ib.placeOrder(obj.ibkr_contract, trade.order)
        print(trade, '\n')
    
        return trade 
    

    def modify_limit_order(self, obj=None, size=None, buy_sell=None, trade=None, price=None, all_or_none=None):
        if trade is None:
            raise ValueError(f"Trade not found")
    
        if trade.orderStatus.status not in self.SAFE_TO_MODIFY:
            #raise ValueError(f"Order {trade.order.orderId} is not in a modifiable state")
            return

        # Only overwrite if explicitly provided
        if price is not None:
            trade.order.lmtPrice = price
       
        if size is not None:
            trade.order.totalQuantity = size

        if buy_sell is not None:
            trade.order.action = buy_sell

        if all_or_none is not None:
            trade.order.allOrNone = all_or_none

        trade = self.ib.placeOrder(obj.ibkr_contract, trade.order)
        # print(trade, '\n')
    
        return trade


    def cancel_order(self, trade):
        if trade is None:
            return

        status = trade.orderStatus.status
        if status in {"Cancelled", "Filled", "Inactive"}:
            return

        self.ib.cancelOrder(trade.order)


    '''
    this is the old code.  needs to be updatedd.
    
    def get_position_size(self, account, contract, position, avgCost):
        super().position(account, contract, position, avgCost)

        objList = [obj for key, obj in self.ibkrDictID.items() if obj.ibkr_contractID == contract.conId]
        if len(objList) == 1:
            obj = objList[0]
            obj.current_position = position
    '''

    async def get_historical_closes_df(self, 
                                       contract_list, 
                                       lookback_period = '1 Y', 
                                       length_of_each_period='1 day',
                                       prices_to_use='TRADES',
                                       use_regular_trading_hours=True,
                                       remove_today=True):

        df_list = []
        for contract in contract_list:

            sym = contract.symbol

            bars = await self.ib.reqHistoricalDataAsync(
                contract=contract,
                endDateTime="",          # "" means now
                durationStr=lookback_period,
                barSizeSetting=length_of_each_period,
                whatToShow=prices_to_use,
                useRTH=use_regular_trading_hours,
                formatDate=1
            )

            df = pd.DataFrame([(bar.date, bar.close) for bar in bars], columns=["date", "close"])
            df['date'] = pd.to_datetime(df['date']).dt.date
            df[sym] = df['close']
            df = df.set_index("date")
            
            df_list.append(df[sym])
        
        closes_df = pd.concat(df_list, axis=1)

        if remove_today:
            today = pd.Timestamp.today().date()
         
            if closes_df.index[-1] == today:
                closes_df = closes_df.iloc[:-1]

        return closes_df

    async def get_avg_daily_volume_df(self, contract_list, timeout=15.0):

        requests = []
        for contract in contract_list:
            ticker = self.ib.reqMktData(
                contract,
                genericTickList="165",
                snapshot=False,
                regulatorySnapshot=False,
            )

            requests.append({
                "contract": contract,
                "ticker": ticker,
                "adv": None,
            })  
        
        deadline = asyncio.get_running_loop().time() + timeout 

        try:
            while any(item["adv"] is None for item in requests):
                for item in requests:
                    if item["adv"] is not None:
                        continue

                    value = item["ticker"].avVolume

                    if value is not None and not math.isnan(value) and value >= 0:
                        # IBKR reports stock average volume in hundreds of shares.
                        item["adv"] = float(value) * 100

                        # This symbol is complete, so release its market-data line.
                        self.ib.cancelMktData(item["contract"])

                if asyncio.get_running_loop().time() >= deadline:
                    for item in requests:
                        if item["adv"] is None:
                            item["adv"] = 0.0
                            self.ib.cancelMktData(item["contract"])
                    '''
                    missing = [
                        item["contract"].localSymbol
                        or item["contract"].symbol
                        for item in requests
                        if item["adv"] is None
                    ]
                     raise TimeoutError(
                        f"Timed out waiting for average volume: {missing}"
                    )
                    '''

                await asyncio.sleep(0.05)

        finally:
            # Also cleans up every unfinished request on timeout or cancellation.
            for item in requests:
                if item["adv"] is None:
                    self.ib.cancelMktData(item["contract"])

        return pd.DataFrame(
                {
                    "symbol": (
                        item["contract"].localSymbol
                        or item["contract"].symbol
                    ),
                    "avg_daily_volume": item["adv"],
                }
                for item in requests
            )
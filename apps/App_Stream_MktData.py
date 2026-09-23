
# CONSTANTS 

IBKR_PORT        = 7496

INPUT_WB_NAME    = "2026 Fin Inst Database.xlsx"
INPUT_WS_NAME    = "MKT DATA STREAMING INPUTS"
INPUT_TBL_NAME   = INPUT_WS_NAME.replace(" ", "_")

CRYPTO_COLS = [ 'my_prod_type',

                'my_fi_name',
                'my_pf_name',

                'numerator_currency',
                'denominator_currency',

                'size_screen_bid',
                'price_screen_bid',
                'price_screen_ask',
                'size_screen_ask',

                'size_unit_bid',
                'price_unit_bid',
                'price_unit_ask',
                'size_unit_ask',

                'size_unit_bid',
                'cf_unit_hit_bid_comm',
                'cf_unit_hit_bid',
                'cf_unit_hit_bid_all_in',
                'cf_unit_lift_ask_all_in',
                'cf_unit_lift_ask',
                'cf_unit_lift_ask_comm',
                'size_unit_ask',
                
                'days_settle_comm',
                'days_settle_trade',
                'days_settle_expiry',
                'days_settle_expiry_near', 	 
                'days_settle_expiry_far',

                'price_screen_close',
                'position']

BTC_ETF_COLS = ['my_fi_name',
                'price_screen_bid',
                'price_screen_ask']


STAT_ARB_COLS = ['my_prod_type',

                 'my_fi_name',
                 'my_pf_name',

                 'size_screen_bid',
                 'price_screen_bid',
                 'price_screen_ask',
                 'size_screen_ask',
                 
                 'price_screen_close',
                 'position']

BO_ATTR_LIST = [('price_unit_bid', max),
                ('price_unit_ask', min),        
                ('cf_unit_hit_bid_all_in', max),
                ('cf_unit_lift_ask_all_in', max)]


# --- python imports ---
import asyncio
from collections import defaultdict
from datetime import datetime
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

# --- system setup ---
import sys
import os
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

# --- utils ---
# from IPython.display import display, clear_output
from input_output.Standard_Input_and_Output import standard_input, standard_output
from input_output.Class_InputOutput         import InputOutput
io = InputOutput()

# --- IBKR ---
from ibkr.Class_IBKR_IB import IBKR_IB
ibkr = IBKR_IB(port=IBKR_PORT)

# --- feeds ---
from ws_feeds.Class_WS_FeedManager import WSFeedManager

# --- builders ---
from fin_insts.Make_Single_Leg_Fin_Insts      import make_single_leg_fin_insts, get_db_df
from fin_insts.tradable.Class_FI_FutureSpread import FutureSpread
from fin_insts.derived.Class_FI_Synthetic     import Synthetic
from fin_insts.derived.Class_FI_BestOf        import BestOf


# this cell contains code for creating and calculating bestOf
def create_bo_objs_list(spot_list, equity_list, futs_list, syn_futs_list):
    bo_spot   = BestOf("SPOT", spot_list, BO_ATTR_LIST)   
    bo_equity = BestOf("ETF", equity_list, BO_ATTR_LIST)   

    futs_dict = defaultdict(list)
    for obj in futs_list:
        futs_dict[obj.my_fi_name].append(obj)
    for obj in syn_futs_list:
        last_name = obj.my_fi_name.split("/")[-1]
        futs_dict[last_name].append(obj)

    bo_objs_list = [bo_spot, bo_equity]
    for key, list_ in futs_dict.items():
        bo_obj = BestOf(key, list_, BO_ATTR_LIST)
        bo_objs_list.append(bo_obj)

    return bo_objs_list


def calc_best_of(bo_objs_list):
    
    for obj in bo_objs_list:

        for obj_ in obj.objs_list:
            
            # print(obj_.my_fi_name, obj_.cf_unit_hit_bid, obj_.comm_unit_hit_bid, obj_.cf_unit_lift_ask, obj_.comm_unit_lift_ask)
            
            cf = getattr(obj_, 'cf_unit_hit_bid', np.nan)
            comm = getattr(obj_, 'cf_unit_hit_bid_comm', np.nan)
            setattr(obj_, 'cf_unit_hit_bid_all_in',  cf  + comm)
            
            cf = getattr(obj_, 'cf_unit_lift_ask', np.nan)
            comm = getattr(obj_, 'cf_unit_lift_ask_comm', np.nan)
            setattr(obj_, 'cf_unit_lift_ask_all_in', cf  + comm)

            # print(obj_.my_fi_name, obj_.cf_unit_hit_bid_all_in, obj_.cf_unit_lift_ask_all_in)
        
        obj.update_best_of_best_only()
        
        for attr, x in BO_ATTR_LIST:

            b_obj = getattr(obj, attr + '_obj')

            if 'bid' in attr:
                new_attr = 'size_unit_bid'
                amt = getattr(b_obj, new_attr, np.nan)
            elif 'ask' in attr:
                new_attr = 'size_unit_ask'
                amt = getattr(b_obj, new_attr, np.nan)

            setattr(obj, new_attr, amt)

            if 'cf' in attr:

                if 'bid' in attr:
                    tail = '_hit_bid'
                elif 'ask' in attr:
                    tail = '_lift_ask'

                amt = getattr(b_obj, 'cf_unit' + tail, np.nan)
                setattr(obj, 'cf_unit' + tail, amt)

                amt = getattr(b_obj, 'cf_unit' + tail + "_comm", np.nan)
                setattr(obj, 'cf_unit' + tail + "_comm", amt)
              
    return bo_objs_list


async def edited_standard_output(input_dict, output_list, bo_objs_list, OUTPUT_COLS, FLATTEN_COLS=None):
    
    refresh      = input_dict.get('timer interval', 10)
    display_mode = input_dict.get('display df onscreen', False)
    csv_mode     = input_dict.get('save df to csv', False)
    xl_mode      = input_dict.get('send df to xl', False)
    print_ts     = input_dict.get('print timestamp onscreen', False)
    add_ts       = input_dict.get('add timestamp to output df', False)

    # print(refresh, display_mode, csv_mode, xl_mode, print_ts, add_ts)

    need_history = any(mode == 'append' for mode in [display_mode, csv_mode, xl_mode])
    history_chunks = []
    history_df = None
    
    if csv_mode != False:
        directory = input_dict['output directory']
        filename  = input_dict['output workbook name']
        path      = os.path.normpath(os.path.join(directory, filename))
    
    if xl_mode != False:
        wb, ws, cell = io.set_xw_book_sheet_and_range(input_dict['output workbook name'],
                                                      input_dict['output worksheet name'],
                                                      input_dict['output cell name'])

    await asyncio.sleep(1)

    while True:

        ts = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d_%H-%M-%S")

        if print_ts:
            print(ts)

        bo_objs_list = calc_best_of(bo_objs_list)
        
        current_df = io.convert_objs_to_printable_df(output_list, CRYPTO_COLS)
    
        if add_ts:
            current_df['time'] = ts

        # print(current_df)
            
        if need_history:
            if current_df is None or current_df.empty:
                # nothing to add → exit this block
                pass
            else:
                history_chunks.append(current_df)
                if display_mode == 'append' or xl_mode == 'append':
                    history_df = pd.concat(history_chunks, ignore_index=True)
            
        if display_mode:
            df = history_df if display_mode == 'append' else current_df
            # clear_output(wait=True)
            # display(df)
            print(df)

        if csv_mode:
            if csv_mode == 'append':
                current_df.to_csv(path, mode='a', header=not os.path.exists(path), index=False)
            else:
                current_df.to_csv(path, index=False)

        if xl_mode:
            df = history_df if xl_mode == 'append' else current_df
            ws.clear_contents()
            cell.options(index=False, header=True).value = df
        
        await asyncio.sleep(refresh)
 
 
async def main():

    wb, ws = io.set_xw_book_and_sheet(INPUT_WB_NAME, INPUT_WS_NAME)
    df     = io.get_xw_df(ws, INPUT_TBL_NAME, table=True)

    crypto_input_dict   = df.set_index("Keys")['Crypto'].to_dict()
    # stat_arb_pairs_input_dict = df.set_index("Keys")['Stat_Arb_Pairs'].to_dict()
    stat_arb_group_input_dict = df.set_index("Keys")['Stat_Arb_Group'].to_dict()
    btc_etf_input_dict  = df.set_index("Keys")['BTC_ETF'].to_dict()
    
    db_df       = get_db_df()
    db_df       = db_df[db_df['mkt_data_stream'] == True]
    crypto_df   = db_df[db_df['grouping'] == 'crypto']
    stat_arb_df = db_df[db_df["grouping"].isin(["fixed income", "sector", "sector alt", "corps"])]

    crypto_objs_list   = make_single_leg_fin_insts(crypto_df)
    stat_arb_objs_list = make_single_leg_fin_insts(stat_arb_df)

    objs_list = [*crypto_objs_list, *stat_arb_objs_list]

    ws_objs_list = [obj for obj in objs_list if obj.my_pf_name != 'IBKR']
    ws_feed      = WSFeedManager(ws_objs_list)

    await ws_feed.complete_fi_objects()   
        
    ibkr_objs_list = [obj for obj in objs_list if obj.my_pf_name == 'IBKR']

    for obj in ibkr_objs_list:
        obj.position = 0

    def position_handler(position):
        for obj in ibkr_objs_list:
            if obj.ibkr_contract.conId == position.contract.conId:
                obj.position = position.position

    if ibkr_objs_list:
        await ibkr.connect()
        print("IBKR connected:", ibkr.ib.isConnected())
        
        await asyncio.gather(*(ibkr.create_simple_contract(obj) for obj in ibkr_objs_list))
        await asyncio.gather(*(ibkr.complete_obj(obj) for obj in ibkr_objs_list))

        ibkr.ib.positionEvent += position_handler

        for position in ibkr.ib.positions():
            position_handler(position)

    #for obj in ibkr_objs_list:
    #   print(obj.ibkr_details)


  

    #''' 
    # insert ibkr BAG instruments here (future_spread, option_spread, option_combo, etc.
    ibkr_futs_list = [obj for obj in ibkr_objs_list if obj.my_prod_type == 'future']

    ibkr_futs_list_btc = [obj for obj in ibkr_futs_list if "BTC" in obj.my_fi_name]
    ibkr_futs_spds_list_btc = FutureSpread.make_spreads(ibkr_futs_list_btc)
    
    ibkr_futs_list_eth = [obj for obj in ibkr_futs_list if "ETH" in obj.my_fi_name]
    ibkr_futs_spds_list_eth = FutureSpread.make_spreads(ibkr_futs_list_eth)
    
    ibkr_futs_spds_list = [*ibkr_futs_spds_list_btc, *ibkr_futs_spds_list_eth]
    await asyncio.gather(*(obj.make_ibkr_spread_contract(ibkr) for obj in ibkr_futs_spds_list))
    #'''                
 
    #''' 
    #insert synthetic instruments here 
    syn_futs_list_btc = Synthetic.make_syn_futures_list(ibkr_futs_list_btc, ibkr_futs_spds_list_btc)
    syn_futs_list_eth = Synthetic.make_syn_futures_list(ibkr_futs_list_eth, ibkr_futs_spds_list_eth)

    syn_futs_list = (*syn_futs_list_btc, *syn_futs_list_eth)
    #''' 

    #''' 
    #insert bestOf instruments here
    spot_list    = [obj for obj in crypto_objs_list if obj.my_prod_type == 'spot']
    equity_list  = [obj for obj in crypto_objs_list if obj.my_prod_type == 'equity']

    bo_objs_list = create_bo_objs_list(spot_list, 
                                       equity_list, 
                                       ibkr_futs_list, 
                                       syn_futs_list)
    #''' 

    output_list = [*bo_objs_list,
                   *spot_list, 
                   *equity_list,
                   *ibkr_futs_list,
                   *ibkr_futs_spds_list,
                   *syn_futs_list]

    # Run all streams concurrently
    tasks = []
    tasks.append(asyncio.create_task(ws_feed.run()))
    if ibkr_objs_list:
        tasks.append(asyncio.create_task(ibkr.start_streams(ibkr_objs_list)))
        tasks.append(asyncio.create_task(ibkr.start_streams(ibkr_futs_spds_list)))

    if crypto_input_dict['active']:
        await asyncio.sleep(10)
        tasks.append(asyncio.create_task(edited_standard_output(crypto_input_dict, output_list, bo_objs_list, CRYPTO_COLS)))

    #if stat_arb_pairs_input_dict['active']:
     #   await asyncio.sleep(10)
      #  tasks.append(asyncio.create_task(standard_output(stat_arb_pairs_input_dict, stat_arb_objs_list, STAT_ARB_COLS)))

    if stat_arb_group_input_dict['active']:
        await asyncio.sleep(10)
        tasks.append(asyncio.create_task(standard_output(stat_arb_group_input_dict, stat_arb_objs_list, STAT_ARB_COLS)))
    
    if btc_etf_input_dict['active']:
        await asyncio.sleep(10)
        tasks.append(asyncio.create_task(standard_output(btc_etf_input_dict, equity_list, BTC_ETF_COLS)))

    await asyncio.gather(*tasks)  # expose this for .py usage


# for vs code usage
asyncio.run(main())

# for .py usage and remember to expose await at end of main and comment out the two autoreload lines
# if __name__ == "__main__":
  # asyncio.run(main())

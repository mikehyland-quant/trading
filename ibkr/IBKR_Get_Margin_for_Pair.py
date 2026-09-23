from ib_insync import IB, Stock, Contract, ComboLeg, MarketOrder, TagValue
from math import gcd
import pandas as pd


# ============================================================
# CONNECT TO IBKR
# ============================================================

ib = IB()
ib.connect('127.0.0.1', 7496, clientId=20)


# ============================================================
# PAIRS
#
# Each tuple is:
# (symbol_1, symbol_2, target_notional_per_side)
#
# BRK.A needs a much larger notional because one share is
# extremely expensive.
# ============================================================

pairs = [
    ('GOOG',  'GOOGL', 100_000),
    ('FOX',   'FOXA',  100_000),
    ('NWSA',  'NWS',   100_000),
    ('HEI A', 'HEI',   100_000),
    ('BRK B', 'BRK A', 1_000_000),
]


# ============================================================
# QUALIFY STOCK CONTRACT
# ============================================================

def get_stock(symbol):

    contract = Stock(
        symbol=symbol,
        exchange='SMART',
        currency='USD'
    )

    qualified = ib.qualifyContracts(contract)

    if not qualified:
        raise Exception(f'Could not qualify contract: {symbol}')

    return qualified[0]


# ============================================================
# GET MARKET PRICE
# ============================================================

def get_price(contract):

    ticker = ib.reqMktData(contract, '', False, False)

    ib.sleep(2)

    price = ticker.marketPrice()

    ib.cancelMktData(contract)

    if price is None or price != price or price <= 0:
        raise Exception(
            f'Could not obtain market price for '
            f'{contract.localSymbol}'
        )

    return float(price)


# ============================================================
# RUN ONE DIRECTION OF A PAIR
#
# Example:
# long_symbol = GOOG
# short_symbol = GOOGL
# ============================================================

def pair_margin(long_symbol, short_symbol, target_notional):

    print(f'Checking LONG {long_symbol} / SHORT {short_symbol}...')

    # --------------------------------------------------------
    # Get contracts
    # --------------------------------------------------------

    long_contract = get_stock(long_symbol)
    short_contract = get_stock(short_symbol)

    # --------------------------------------------------------
    # Get prices
    # --------------------------------------------------------

    long_price = get_price(long_contract)
    short_price = get_price(short_contract)

    # --------------------------------------------------------
    # Calculate approximately dollar-neutral share quantities
    # --------------------------------------------------------

    long_shares = round(target_notional / long_price)
    short_shares = round(target_notional / short_price)

    if long_shares < 1:
        long_shares = 1

    if short_shares < 1:
        short_shares = 1

    # --------------------------------------------------------
    # Reduce shares to combo ratios
    #
    # Example:
    # 284 shares vs 281 shares
    #
    # gcd may be 1, meaning combo ratios remain 284:281.
    # --------------------------------------------------------

    common = gcd(long_shares, short_shares)

    long_ratio = long_shares // common
    short_ratio = short_shares // common

    combo_quantity = common

    # --------------------------------------------------------
    # Construct BAG combo
    # --------------------------------------------------------

    combo = Contract()

    combo.symbol = long_symbol
    combo.secType = 'BAG'
    combo.currency = 'USD'
    combo.exchange = 'SMART'

    combo.comboLegs = [

        ComboLeg(
            conId=long_contract.conId,
            ratio=long_ratio,
            action='BUY',
            exchange='SMART'
        ),

        ComboLeg(
            conId=short_contract.conId,
            ratio=short_ratio,
            action='SELL',
            exchange='SMART'
        )
    ]

    # --------------------------------------------------------
    # What-If market order
    # --------------------------------------------------------

    order = MarketOrder(
        action='BUY',
        totalQuantity=combo_quantity
    )

    # Required by IBKR for SMART-routed stock combos
    order.smartComboRoutingParams = [
        TagValue('NonGuaranteed', '1')
    ]

    # --------------------------------------------------------
    # Submit What-If
    # --------------------------------------------------------

    state = ib.whatIfOrder(combo, order)

    # --------------------------------------------------------
    # Validate response
    # --------------------------------------------------------

    if not hasattr(state, 'initMarginChange'):

        raise Exception(
            f'IBKR What-If failed. Returned: {state}'
        )

    # --------------------------------------------------------
    # Margin fields
    # --------------------------------------------------------

    init_before = float(state.initMarginBefore)
    init_after = float(state.initMarginAfter)
    init_change = float(state.initMarginChange)

    maint_before = float(state.maintMarginBefore)
    maint_after = float(state.maintMarginAfter)
    maint_change = float(state.maintMarginChange)

    # --------------------------------------------------------
    # Dollar exposures
    # --------------------------------------------------------

    long_notional = long_shares * long_price
    short_notional = short_shares * short_price

    gross_notional = long_notional + short_notional
    net_notional = long_notional - short_notional

    # --------------------------------------------------------
    # Effective pair margin percentages
    # --------------------------------------------------------

    init_margin_pct = (
        init_change / gross_notional
        if gross_notional != 0
        else None
    )

    maint_margin_pct = (
        maint_change / gross_notional
        if gross_notional != 0
        else None
    )

    return {

        'Long': long_symbol,
        'Short': short_symbol,

        'Long Price': long_price,
        'Short Price': short_price,

        'Long Shares': long_shares,
        'Short Shares': short_shares,

        'Long Notional': long_notional,
        'Short Notional': short_notional,

        'Gross Notional': gross_notional,
        'Net Notional': net_notional,

        'Init Margin Before': init_before,
        'Init Margin After': init_after,
        'Init Margin Change': init_change,

        'Maint Margin Before': maint_before,
        'Maint Margin After': maint_after,
        'Maint Margin Change': maint_change,

        'Init Margin % Gross': init_margin_pct,
        'Maint Margin % Gross': maint_margin_pct,
    }


# ============================================================
# RUN ALL PAIRS IN BOTH DIRECTIONS
# ============================================================

results = []

for symbol1, symbol2, target_notional in pairs:

    directions = [
        (symbol1, symbol2),
        (symbol2, symbol1),
    ]

    for long_symbol, short_symbol in directions:

        try:

            result = pair_margin(
                long_symbol=long_symbol,
                short_symbol=short_symbol,
                target_notional=target_notional
            )

            results.append(result)

        except Exception as e:

            print(
                f'ERROR LONG {long_symbol} / '
                f'SHORT {short_symbol}: {e}'
            )


# ============================================================
# CREATE DATAFRAME
# ============================================================

df = pd.DataFrame(results)


if not df.empty:

    # Convert decimal percentages to actual percentages
    df['Init Margin % Gross'] = (
        df['Init Margin % Gross'] * 100
    )

    df['Maint Margin % Gross'] = (
        df['Maint Margin % Gross'] * 100
    )

    # --------------------------------------------------------
    # Formatting
    # --------------------------------------------------------

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 250)

    print('\n\nRESULTS\n')

    columns_to_show = [

        'Long',
        'Short',

        'Long Price',
        'Short Price',

        'Long Shares',
        'Short Shares',

        'Long Notional',
        'Short Notional',

        'Gross Notional',

        'Init Margin Change',
        'Init Margin % Gross',

        'Maint Margin Change',
        'Maint Margin % Gross',
    ]

    print(
        df[columns_to_show].to_string(
            index=False,
            float_format=lambda x: f'{x:,.2f}'
        )
    )


# ============================================================
# DISCONNECT
# ============================================================

ib.disconnect()
# ==========================================
# SHIVAY AI PRO v2.5
# Sector Strength Engine
# ==========================================

SECTORS = {

    "BANKING": [
        "HDFCBANK FUT",
        "ICICIBANK FUT",
        "SBIN FUT",
        "AXISBANK FUT",
        "KOTAKBANK FUT",
        "INDUSINDBK FUT",
        "PNB FUT",
    ],

    "IT": [
        "TCS FUT",
        "INFY FUT",
        "HCLTECH FUT",
        "TECHM FUT",
        "WIPRO FUT",
        "LTIM FUT",
        "PERSISTENT FUT",
    ],

    "AUTO": [
        "TATAMOTORS FUT",
        "M&M FUT",
        "MARUTI FUT",
        "BAJAJ-AUTO FUT",
        "TVSMOTOR FUT",
        "EICHERMOT FUT",
    ],

    "POWER": [
        "RELIANCE FUT",
        "ONGC FUT",
        "BPCL FUT",
        "IOC FUT",
        "GAIL FUT",
        "NTPC FUT",
        "POWERGRID FUT",
        "TATAPOWER FUT",
    ],

    "METAL": [
        "TATASTEEL FUT",
        "JSWSTEEL FUT",
        "HINDALCO FUT",
        "VEDL FUT",
    ],

    "DEFENCE": [
        "HAL FUT",
        "BEL FUT",
    ],

    "PHARMA": [
        "SUNPHARMA FUT",
        "CIPLA FUT",
        "DRREDDY FUT",
        "DIVISLAB FUT",
        "LUPIN FUT",
        "TORNTPHARM FUT",
        "ZYDUSLIFE FUT",
    ],

    "FMCG": [
        "ITC FUT",
        "HINDUNILVR FUT",
        "NESTLEIND FUT",
    ],

    "RETAIL": [
        "TRENT FUT",
        "DMART FUT",
    ],

    "CAPITAL": [
        "LT FUT",
        "SIEMENS FUT",
        "ABB FUT",
        "BHEL FUT",
        "CUMMINSIND FUT",
    ],

}


def get_sector(symbol):

    for sector, stocks in SECTORS.items():

        if symbol in stocks:
            return sector

    return "OTHER"


def sector_priority(symbol):

    sector = get_sector(symbol)

    priority = {

        "BANKING": 100,

        "POWER": 96,

        "DEFENCE": 94,

        "AUTO": 90,

        "CAPITAL": 88,

        "IT": 86,

        "METAL": 82,

        "RETAIL": 80,

        "FMCG": 78,

        "PHARMA": 75,

        "OTHER": 50,

    }

    return priority.get(sector, 50)
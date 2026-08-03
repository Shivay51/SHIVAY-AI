def late_entry_filter(price, day_high, day_low, atr):

    day_range = day_high - day_low

    # બહુ જ વધારે ચાલેલો Stock હોય તો જ Reject
    if day_range >= atr * 8:
        return False

    # High થી બહુ જ નજીક હોય ત્યારે જ Reject
    if (day_high - price) <= atr * 0.10:
        return False

    return True
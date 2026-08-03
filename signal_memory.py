# ==========================================
# SHIVAY AI PRO
# Signal Memory
# ==========================================

_sent_signals = set()


# ==========================================
# CHECK SIGNAL
# ==========================================

def signal_exists(symbol):

    if not symbol:
        return False

    return symbol in _sent_signals


# ==========================================
# ADD SIGNAL
# ==========================================

def add_signal(symbol):

    if not symbol:
        return

    _sent_signals.add(symbol)


# ==========================================
# REMOVE SIGNAL
# ==========================================

def remove_signal(symbol):

    if not symbol:
        return

    _sent_signals.discard(symbol)


# ==========================================
# CLEAR ALL
# ==========================================

def clear_signals():

    _sent_signals.clear()


# ==========================================
# TOTAL SIGNALS
# ==========================================

def total_signals():

    return len(_sent_signals)


# ==========================================
# GET ALL SIGNALS
# ==========================================

def get_all_signals():

    return list(_sent_signals)
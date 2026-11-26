"""Shared wall-clock schedule for all provider availability checks."""
from datetime import datetime,timedelta
import math


def next_check_at(last_check,interval_seconds):
    """Next fixed local-clock slot, never an elapsed delay after a manual check.

    A local Monday anchor also keeps daily/weekly and non-divisor intervals
    deterministic. Four hours produces 00/04/08/12/16/20; fractional intervals
    retain their configured minute boundaries. The process timezone applies.
    """
    if not last_check:return 0
    if not math.isfinite(interval_seconds) or interval_seconds<=0:raise ValueError('Invalid check interval.')
    anchor=datetime(1970,1,5)
    local=datetime.fromtimestamp(last_check)
    slot=math.floor((local-anchor).total_seconds()/interval_seconds)+1
    while True:
        candidate=anchor+timedelta(seconds=slot*interval_seconds)
        timestamp=candidate.timestamp()
        if timestamp>last_check:return timestamp
        slot+=1

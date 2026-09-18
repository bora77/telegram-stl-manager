"""Observed file-growth speed. Monotonic timing excludes later NAS delivery."""
from collections import deque
from datetime import datetime,timezone,timedelta
import time

class TransferMeter:
    def __init__(self,clock=time.monotonic,started_at=None):
        self.clock=clock;self.start=clock()
        self.started_at=started_at or datetime.now(timezone.utc)
        self.samples=deque([(self.start,0)])
        self.previous=0;self.last_growth=self.start
    def sample(self,done,finished=False):
        now=self.clock()
        if done<self.previous:raise ValueError('Byte count cannot move backwards within one transfer attempt.')
        if done>self.previous:self.last_growth=now
        self.previous=done;self.samples.append((now,done))
        while len(self.samples)>2 and self.samples[1][0]<=now-5:self.samples.popleft()
        elapsed=max(0,(self.last_growth if finished else now)-self.start)
        interval=now-self.samples[0][0]
        current=(done-self.samples[0][1])/interval if interval>0 else None
        return {'download_started_at':self.started_at.isoformat(),
                'download_finished_at':(self.started_at+timedelta(seconds=elapsed)).isoformat() if finished else None,
                'download_seconds':elapsed,'download_speed_bps':None if finished else current,
                'download_average_bps':done/elapsed if elapsed>0 else None}

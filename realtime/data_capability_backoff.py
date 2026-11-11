"""Trusted bounded retry policy; never supplied in customer request parameters."""
from dataclasses import dataclass,asdict
from datetime import timedelta
import time


@dataclass(frozen=True)
class RetryPolicy:
    maximum_attempts: int = 1
    initial_delay_seconds: int = 1
    maximum_delay_seconds: int = 30
    maximum_window_seconds: int = 120

    def __post_init__(self):
        limits={'maximum_attempts':(1,8),'initial_delay_seconds':(1,86400),'maximum_delay_seconds':(1,86400),'maximum_window_seconds':(1,86400)}
        for name,(lo,hi) in limits.items():
            n=getattr(self,name)
            if type(n) is not int or not lo<=n<=hi:raise ValueError('invalid bounded retry policy')
        if self.initial_delay_seconds>self.maximum_delay_seconds:raise ValueError('initial delay exceeds cap')

    def public(self):return asdict(self)

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict) or set(value)!=set(cls.__dataclass_fields__):raise ValueError('exact retry policy fields required')
        return cls(**value)

    def delay(self,attempt,retry_after=0):
        if type(attempt) is not int or not 1<=attempt<=self.maximum_attempts:raise ValueError('failure number outside policy')
        if type(retry_after) is not int or not 0<=retry_after<=86400:raise ValueError('bounded Retry-After required')
        return max(retry_after,min(self.maximum_delay_seconds,self.initial_delay_seconds*2**(attempt-1)))


class RetryWindow:
    """Wait only within BOTH wall-clock authorization and monotonic retry window."""
    def __init__(self,policy,clock,deadline,*,sleep=None,monotonic=None):
        self.policy,self.clock,self.deadline=policy,clock,deadline
        self.sleep,self.monotonic=sleep or time.sleep,monotonic or time.monotonic
        self.started=clock();self.tick=self.monotonic()

    def remaining(self):
        return min((self.deadline-self.clock()).total_seconds(),(self.started+timedelta(seconds=self.policy.maximum_window_seconds)-self.clock()).total_seconds(),self.policy.maximum_window_seconds-(self.monotonic()-self.tick))

    def wait(self,seconds,canceled,authorize):
        target=self.clock()+timedelta(seconds=seconds)
        cutoff=min(self.deadline,self.started+timedelta(seconds=self.policy.maximum_window_seconds))
        elapsed=self.monotonic()-self.tick
        if target>=cutoff or elapsed+seconds>=self.policy.maximum_window_seconds:return False
        stop_tick=self.monotonic()+seconds
        while self.clock()<target or self.monotonic()<stop_tick:
            if self.clock()>=cutoff or self.monotonic()-self.tick>=self.policy.maximum_window_seconds:return False
            if canceled():return False
            authorize()
            remaining=max((target-self.clock()).total_seconds(),stop_tick-self.monotonic())
            self.sleep(min(.1,max(.001,remaining)))
        if canceled() or self.clock()>=cutoff:return False
        authorize();return True


class RetryNotEntered(Exception):
    """A next call was refused before source entry; prior bills remain known."""
    def __init__(self,code):self.code=code;super().__init__(code)


class SourceRetrySession:
    """One job-wide window/count/budget across native legs; no adapter replay."""
    def __init__(self,context,policy,ceiling,clock,authorize,*,sleep=None,monotonic=None):
        from .data_capability_jobs import amount
        self.ctx,self.policy,self.clock,self.authorize=context,policy,clock,authorize
        if context.source_retry_policy!=policy.public():raise RetryNotEntered('source_retry_not_admitted')
        if (not all(callable(v) for v in (context.before_call,context.confirm_bill,context.defer_calls,getattr(context.response,'checkpoint',None)))
                or ceiling is None):raise RetryNotEntered('source_call_journal_unavailable')
        self.ceiling,self.maximum=amount(ceiling),amount(context.maximum_cost)
        self.window=RetryWindow(policy,clock,context.deadline,sleep=sleep,monotonic=monotonic)
        self.spent=amount('0');self.calls=0;self.failures=0;self.overrun=False

    def _wait(self,seconds,cutoff):
        if self.clock()+timedelta(seconds=seconds)>=cutoff:return False
        def authorized():
            if self.clock()>=cutoff:raise RetryNotEntered('source_expansion_window_expired')
            self.authorize()
        try:return self.window.wait(seconds,self.ctx.canceled,authorized)
        except Exception:return False

    def enter(self,phase,cutoff):
        from .data_capability_executor import SourceCallDeferred
        from .product import ProductError
        if self.calls>=self.policy.maximum_attempts:raise RetryNotEntered('source_call_limit')
        if self.overrun or self.spent+self.ceiling>self.maximum:raise RetryNotEntered('source_call_budget_exceeded')
        while True:
            if self.ctx.canceled():raise RetryNotEntered('source_canceled_before_call')
            if self.clock()>=cutoff:raise RetryNotEntered('source_expansion_window_expired')
            remaining=min(self.window.remaining(),(cutoff-self.clock()).total_seconds())
            if remaining<=0:raise RetryNotEntered('source_retry_window_expired')
            try:
                self.authorize();self.ctx.before_call(phase,str(self.ceiling))
            except SourceCallDeferred as exc:
                if not self._wait(exc.retry_after,cutoff):raise RetryNotEntered('source_retry_window_unavailable')
                continue
            except ProductError as exc:raise RetryNotEntered(exc.code) from None
            self.calls+=1
            # Recompute after the durable local gate has consumed wall time.
            return min(cutoff,self.ctx.deadline,self.clock()+timedelta(seconds=max(0,self.window.remaining())))

    def confirmed(self,cost):
        from .data_capability_jobs import amount
        cost=amount(cost);self.spent+=cost;self.overrun=self.overrun or cost>self.ceiling

    def again(self,retry_after,cutoff):
        self.failures+=1
        delay=self.policy.delay(self.failures,retry_after)
        self.ctx.defer_calls(delay)
        if self.calls>=self.policy.maximum_attempts or self.overrun or self.spent+self.ceiling>self.maximum:return False
        return self._wait(delay,cutoff)

    def successful_leg(self):self.failures=0

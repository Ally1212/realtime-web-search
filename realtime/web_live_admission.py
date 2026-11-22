"""Reserve a live Web exit and its two shared rate slots together."""
from .discovery import GoogleBlocked


class AdmissionDeferred(Exception):
    def __init__(self, code, wait):
        self.code, self.wait = code, wait


def reserve_live_web(store, config, proxy_hash, locale, interval):
    wait = 0.0
    try:
        with store.connect() as connection:
            with connection.transaction():
                # Lock a physical exit before either shared source row. A busy
                # exit must not advance global request pacing.
                ready, delay = store.reserve_google_proxy(
                    proxy_hash, locale, interval, connection=connection, recheck_quarantine=True)
                if not ready:
                    return False, delay
                for source in ('google_openserp', 'google_web'):
                    slot = store.acquire_discovery_slot(
                        source, config.google_web_initial_rps, maximum_rps=config.google_web_max_rps,
                        connection=connection, max_wait_seconds=2)
                    if not slot['allowed']:
                        code = ('google_web_rate_wait' if slot['state']=='rate_wait'
                                else 'google_web_circuit_open' if source=='google_web'
                                else 'google_provider_cooling')
                        # Also roll back the first slot and the exit.
                        raise AdmissionDeferred(code, slot['wait'])
                    wait = max(wait, slot['wait'])
    except AdmissionDeferred as exc:
        error = GoogleBlocked(exc.code)
        error.retry_after_seconds = exc.wait
        raise error from None
    return True, wait

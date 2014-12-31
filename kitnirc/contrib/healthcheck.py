import logging
import os
import threading
import time

from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class HealthcheckModule(Module):
    """A KitnIRC module which checks connection health.

    By default, this module will request a PONG response from the server
    if it hasn't seen any traffic in the past minute, and will assume the
    connection has dropped and exit the process if it doesn't see any traffic
    for 90 seconds.

    These delays can be changed by setting "delay" and "timeout" under the
    [healthcheck] configuration section.
    """

    def __init__(self, *args, **kwargs):
        super(HealthcheckModule, self).__init__(*args, **kwargs)
        config = self.controller.config

        if config.has_option("healthcheck", "delay"):
            self.delay = config.getint("healthcheck", "delay")
        else:
            self.delay = 60

        if config.has_option("healthcheck", "timeout"):
            self.timeout = config.getint("healthcheck", "timeout")
        else:
            self.timeout = 90

        assert self.timeout > self.delay

        self.last_activity = time.time()
        self._stop = False
        self.thread = threading.Thread(target=self.loop, name='healthcheck')
        self.thread.daemon = True

    def start(self, *args, **kwargs):
        super(HealthcheckModule, self).start(*args, **kwargs)
        self._stop = False
        self.thread.start()

    def stop(self, *args, **kwargs):
        super(HealthcheckModule, self).stop(*args, **kwargs)
        self._stop = True
        # In any normal circumstances, the healthcheck thread should finish
        # in about a second or less. We'll give it a little extra buffer.
        self.thread.join(2.0)
        if self.thread.is_alive():
            _log.warning("Healthcheck thread alive 2s after shutdown request.")

    def loop(self):
        _log.info("Healthcheck running: delay=%d timeout=%d",
                  self.delay, self.timeout)
        while not self._stop:
            elapsed = time.time() - self.last_activity

            if elapsed > self.timeout:
                _log.fatal("No incoming in last %d seconds - exiting.", elapsed)
                logging.shutdown()
                # We use this instead of sys.exit because the latter just raises
                # SystemExit in this thread, causing the thread to shut down.
                os._exit(os.EX_IOERR)
            elif elapsed > self.delay:
                _log.debug("Sending healthcheck ping...")
                self.controller.client.ping()

            time.sleep(1)

    @Module.handle("ACTIVITY")
    def activity(self, client):
        self.last_activity = time.time()


module = HealthcheckModule

# vim: set ts=4 sts=4 sw=4 et:

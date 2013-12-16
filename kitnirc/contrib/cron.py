import datetime
import logging
import random
import threading
import time

from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class Cron(object):
    """An individual cron entry."""

    def __init__(self, event, seconds, minutes, hours):
        self.event = event
        self.seconds = self.parse_time_field(seconds, 60)
        self.minutes = self.parse_time_field(minutes, 60)
        self.hours = self.parse_time_field(hours, 24)
        now = datetime.datetime.now().replace(microsecond=0)
        self.next_fire = self.calculate_next_fire(now)

    def parse_time_field(self, inputstr, count):
        values = set()

        for item in inputstr.split(","):
            # See if it's just a single number.
            try:
                values.add(int(item))
                continue
            except ValueError:
                pass

            # ? can be used to specify "a single random value"
            if item.startswith('?'):
                # With an optional /X to specify "every Xth value,
                # offset randomly"
                _, _, divisor = item.partition("/")
                if divisor:
                    divisor = int(divisor)
                    offset = random.randint(0, divisor-1)
                    values.update(range(offset, count, divisor))
                else:
                    values.add(random.randint(0, count-1))
                continue

            # * can be used to specify "all values"
            if item.startswith("*"):
                # With an optional /X to specify "every Xth value"
                _, _, divisor = item.partition("/")
                if divisor:
                    values.update(range(0, count, int(divisor)))
                else:
                    values.update(range(count))
                continue

            _log.warning("Ignoring invalid specifier '%s' for cron event '%s'",
                item, self.event)

        # Ensure only values within the proper range are utilized
        return sorted(val for val in values if 0 <= val < count)


    def calculate_next_fire(self, after):
        # Keeps track of if we've already moved a field by at least
        # one notch, so that other fields are allowed to stay the same.
        equal_okay = False

        next_second = self.seconds[0]
        for second in self.seconds:
            if second > after.second:
                next_second = second
                equal_okay = True
                break

        next_minute = self.minutes[0]
        for minute in self.minutes:
            if equal_okay and minute == after.minute:
                next_minute = minute
                break
            elif minute > after.minute:
                next_minute = minute
                equal_okay = True
                break

        next_hour = self.hours[0]
        for hour in self.hours:
            if equal_okay and hour == after.hour:
                next_hour = hour
                break
            elif hour > after.hour:
                next_hour = hour
                break

        next_fire = after.replace(hour=next_hour, minute=next_minute,
                                second=next_second, microsecond=0)

        # If we need to roll over to the next day...
        if next_fire <= after:
            next_fire += datetime.timedelta(days=1)

        return next_fire

    def maybe_fire(self, client, after, upto):
        if self.next_fire is None:
            return
        if after < self.next_fire <= upto:
            _log.debug("Cron event '%s' firing.", self.event)
            client.dispatch_event(self.event)
            self.next_fire = self.calculate_next_fire(upto)


class CronModule(Module):
    """A KitnIRC module which provides other modules with scheduling.

    Note: due to how this module interacts with other modules, reloading
    it without reloading other modules will result in previously added
    crons being wiped. If you need to reload this module, you should
    probably just reload all modules.
    """

    def __init__(self, *args, **kwargs):
        super(CronModule, self).__init__(*args, **kwargs)
        self.crons = []
        self.last_tick = datetime.datetime.now()
        self.thread = threading.Thread(target=self.loop, name='cron')
        self.thread.daemon = True
        self._stop = False

    def start(self, *args, **kwargs):
        super(CronModule, self).start(*args, **kwargs)
        self._stop = False
        self.last_tick = datetime.datetime.now()
        self.thread.start()

    def stop(self, *args, **kwargs):
        super(CronModule, self).stop(*args, **kwargs)
        self._stop = True
        # In any normal circumstances, the cron thread should finish in
        # about half a second or less. We'll give it a little extra buffer.
        self.thread.join(1.0)
        if self.thread.is_alive():
            _log.warning("Cron thread alive 1s after shutdown request.")

    def loop(self):
        while not self._stop:
            # Use a single "now" for all crons, to ensure consistency
            # relative to the next last_tick value.
            now = datetime.datetime.now().replace(microsecond=0)

            for cron in self.crons:
                cron.maybe_fire(self.controller.client, self.last_tick, now)

            self.last_tick = now
            # Wake up every half-second or so to avoid missing seconds
            time.sleep(0.5)

    @Module.handle("ADDCRON")
    def add_cron(self, client, event, seconds="*", minutes="*", hours="*"):
        """Add a cron entry.

        The arguments for this event are:
            1. The name of the event to dispatch when the cron fires.
            2. What seconds to trigger on, as a timespec (default "*")
            3. What minutes to trigger on, as a timespec (default "*")
            4. What hours to trigger on, as a timespec (default "*")

        Timespecs may be omitted in reverse order of frequency - if hours
        is omitted, the previous timespecs will be applied every hour. If
        both hours and minutes are omitted, the seconds timespec will be
        applied every minute of every hour, and if all timespecs are omitted,
        the event will fire each second.

        Timespecs are strings in the following formats:

            Plain integer - specifies that exact value for the unit.
            "?"   - specifies a random value from 0 to the unit max.
            "?/X" - specifies all multiples of X for this unit, randomly offset
                    by a fixed amount (e.g. ?/15 might become 4,19,34,49).
            "*"   - specifies all values for the unix from 0 to max.
            "*/X" - specifies all multiples of X for the unit.

        Any number of these can be combined in a comma-separated list.
        For instance, "*/15" would be the same as "0,15,30,45" if used
        in the seconds field.
        """
        for cron in self.crons:
            if cron.event == event:
                _log.warning("Cron '%s' is already registered.", event)
                return True

        _log.info("Registering cron for '%s'.", event)
        cron = Cron(event, seconds, minutes, hours)
        self.crons.append(cron)
        return True

    @Module.handle("REMOVECRON")
    def remove_cron(self, client, event):
        """Remove a cron entry by event name."""
        for index, cron in enumerate(self.crons):
            if cron.event == event:
                _log.info("De-registering cron '%s'.", event)
                # Yes, we're modifying the list we're iterating over, but
                # we immediate stop iterating so it's okay.
                self.crons.pop(index)
                break
        return True


module = CronModule


# vim: set ts=4 sts=4 sw=4 et:

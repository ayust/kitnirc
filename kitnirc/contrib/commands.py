import logging
import shlex

from kitnirc.client import Channel
from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class CommandsModule(Module):
    """A KitnIRC module which provides command dispatch.

    Other modules register commands via the ADDCOMMAND event
    (and can also remove commands when they shut down via the
    REMOVECOMMAND event). Each command is associated with an
    event and optionally some help text.

    When a given command is invoked, this module triggers the
    associated event, including the shell-style arguments that
    follow that command. Commands are accepted with no prefix
    in PMs, any other invocation must be prefixed by the sigil
    or the current nick and a separator.

    This module also triggers the COMMANDS event if it needs other
    modules to re-register their commands (e.g. after this module
    has been reloaded).

    Command removal must also specify the event it expects to be
    unlinking the command from - this is used to avoid one module
    unlinking a command that a different module won the race for.

    Incoming events:
      ADDCOMMAND    command event [helptext]
      REMOVECOMMAND command event

    Outgoing events:
      COMMANDS      n/a
      *             command args...
    """


    def __init__(self, *args, **kwargs):
        super(CommandsModule, self).__init__(*args, **kwargs)
        self.prefixes = set()

    def start(self, *args, **kwargs):
        super(CommandsModule, self).start(*args, **kwargs)
        config = self.controller.config

        self.commands = {}

        if config.has_option("command", "sigil"):
            self.sigil = config.get("command", "sigil")
        else:
            self.sigil = None

        if hasattr(self.controller.client, "user"):
            self.regenerate_prefixes()
        self.request_commands(self.controller.client)

    @Module.handle("STARTUP")
    def request_commands(self, client, *args):
        # Broadcast the event which instructs other modules to register
        # their commands. Tying this to an event instead of module starts
        # allows the Commands module to also request re-registration if
        # it is reloaded.
        self.trigger_event("COMMANDS", client, [])

    @Module.handle("WELCOME")
    def regenerate_prefixes(self, *args):
        """Regenerate the cache of command prefixes based on nick etc."""
        nick = self.controller.client.user.nick
        self.prefixes = set([
            nick + ": ",
            nick + ", ",
            nick + " - ",
        ])
        # Include lower-case versions as well, but not caps
        self.prefixes.update([p.lower() for p in self.prefixes])
        if self.sigil:
            self.prefixes.add(self.sigil)

    def check_for_interest(self, client, recipient, message):
        """Determine whether this line is addressing us."""
        for prefix in self.prefixes:
            if message.startswith(prefix):
                return True, message[len(prefix):]

        # Don't require a prefix if addressed in PM.
        # This comes after the prefix checks because
        # if the user does include a prefix, we want
        # to strip it, even in PM.
        if not isinstance(recipient, Channel):
            return True, message

        return False, None

    def parse_command(self, string):
        """Parse out any possible valid command from an input string."""
        possible_command, _, rest = string.partition(" ")
        # Commands are case-insensitive, stored as lowercase
        possible_command = possible_command.lower()
        if possible_command not in self.commands:
            return None, None

        event = self.commands[possible_command]["event"]
        args = shlex.split(rest.strip())
        return event, args

    @Module.handle("NICK")
    def nick(self, client, old_nick, new_nick):
        if new_nick == client.user.nick:
            self.regenerate_prefixes()

    @Module.handle("ADDCOMMAND")
    def add_command(self, client, command, event, helptext=None):
        command = command.lower()
        if command in self.commands:
            _log.warning("Not adding command '%s' - already added.", command)
            return
        _log.info("Adding command '%s' => '%s'.", command, event)
        self.commands[command] = {
            "event": event,
            "help": helptext,
        }

    @Module.handle("REMOVECOMMAND")
    def remove_command(self, client, command, event):
        command = command.lower()
        if command in self.commands:
            linked_event = self.commands[command]["event"]
            if event == linked_event:
                _log.info("Removing command '%s' => '%s'.", command, event)
                del self.commands[command]
            else:
                _log.warning("Not removing command '%s' ('%s' != '%s').",
                             command, event, linked_event)
        else:
            _log.warning("Not removing command '%s' - not added.", command)

    @Module.handle("PRIVMSG")
    def privmsg(self, client, actor, recipient, message):
        parsable, rest = self.check_for_interest(client, recipient, message)
        if not parsable:
            return False

        event, args = self.parse_command(rest)
        if not event:
            return False

        _log.debug("Dispatching event '%s' (command invocation).", event)
        return self.trigger_event(event, client, [actor, recipient] + args)


module = CommandsModule


# vim: set ts=4 sts=4 sw=4 et:

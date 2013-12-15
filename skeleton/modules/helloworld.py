import logging

from kitnirc.client import Channel
from kitnirc.modular import Module


# This is the standard way to get a logger for your module
# via the Python logging library.
_log = logging.getLogger(__name__)


# KitnIRC modules always subclass kitnirc.modular.Module
class HelloWorldModule(Module):
    """A basic KitnIRC module which responds to messages."""

    # This decorator tells KitnIRC what events to route to the
    # function it decorates. The name of the function itself
    # doesn't matter - call it what makes sense.
    @Module.handle("PRIVMSG")
    def respond(self, client, actor, recipient, message):
        if isinstance(recipient, Channel):
            # Only pay attention if addressed directly in channels
            if not message.startswith("%s:" % client.user.nick):
                return
            # Remove our being addressed from the message
            message = message.split(":", 1)[1]

        message = message.strip()

        # Log a message to the INFO log level - see here for more details:
        # http://docs.python.org/2/library/logging.html
        _log.info("Responding to %r in %r", actor, recipient)

        # The 'reply' function automatically sends a replying PM if
        # the bot was PM'd, or addresses the user in a channel who
        # addressed the bot in a channel.
        client.reply(recipient, actor, "Hello, welcome to my world!")
        client.reply(recipient, actor, "I saw you say '%s'." % message)

        # Stop any other modules from handling this message.
        return True


# Let KitnIRC know what module class it should be loading.
module = HelloWorldModule

# vim: set ts=4 sts=4 sw=4 et:

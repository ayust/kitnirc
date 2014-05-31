import logging

from kitnirc.client import Channel
from kitnirc.modular import Module
from kitnirc.user import User


_log = logging.getLogger(__name__)


def is_admin(controller, client, actor):
    """Used to determine whether someone issuing a command is an admin.

    By default, checks to see if there's a line of the type nick=host that
    matches the command's actor in the [admins] section of the config file,
    or a key that matches the entire mask (e.g. "foo@bar" or "foo@bar=1").
    """
    config = controller.config
    if not config.has_section("admins"):
        logging.debug("Ignoring is_admin check - no [admins] config found.")
        return False
    for key,val in config.items("admins"):
        if actor == User(key):
            logging.debug("is_admin: %r matches admin %r", actor, key)
            return True
        if actor.nick.lower() == key.lower() and actor.host.lower() == val.lower():
            logging.debug("is_admin: %r matches admin %r=%r", actor, key, val)
            return True
    logging.debug("is_admin: %r is not an admin.", actor)
    return False


class AdminModule(Module):
    """A KitnIRC module which provides admin functionality.

    Customization of what an "admin" is can be done by overriding the
    is_admin global function in this file.
    """

    @Module.handle("PRIVMSG")
    def privmsg(self, client, actor, recipient, message):
        if isinstance(recipient, Channel):
            # Only pay attention if addressed directly in channels
            if not message.startswith("%s:" % client.user.nick):
                return
            message = message.split(":", 1)[1]

        message = message.strip()
        args = message.split()

        # Ignore empty messages
        if not args:
            return
        command, args = args[0], args[1:]
        command = command.lower()

        available_commands = {
            'join': self.join,
            'part': self.part,
            'quit': self.quit,
            'reload': self.reload,
            'reloadall': self.reloadall,
            'load': self.load,
            'unload': self.unload,
        }

        # Only pay attention to valid commands
        func = available_commands.get(command)
        if not func:
            return

        # Only pay attention to admins
        actor = User(actor)
        if not is_admin(self.controller, client, actor):
            client.reply(recipient, actor, "You are not allowed to do that.")
            return

        result = func(client, args)
        if result is True:
            client.reply(recipient, actor, "Okay.")
        elif result is False:
            client.reply(recipient, actor, "Sorry, try again.")

        # Suppress further handling of the PRIVMSG event.
        return True

    def join(self, client, args):
        if not args:
            return False
        if client.join(args[0], args[1] if len(args) > 1 else None):
            return True
        else:
            return False

    def part(self, client, args):
        if not args:
            return False
        if client.part(args[0]):
            return True
        else:
            return False

    def quit(self, client, args):
        # We immediately disconnect, so no reply
        client.quit()

    def reload(self, client, args):
        if not args:
            return False
        return all(self.controller.reload_module(mod) for mod in args)

    def reloadall(self, client, args):
        return self.controller.reload_modules()

    def load(self, client, args):
        if not args:
            return False
        return self.controller.load_module(args[0])

    def unload(self, client, args):
        if not args:
            return False
        return self.controller.unload_module(args[0])


module = AdminModule

# vim: set ts=4 sts=4 sw=4 et:

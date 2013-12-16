import logging

from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class FreenodeModule(Module):
    """A KitnIRC module which provides Freenode-specific functionality.

    Freenode is irc.freenode.net and runs ircd-seven, a Freenode-specific
    branch of the charybdis ircd. Functionality provided by this module:

        1. If the configuration has a 'password' field in the [nickserv]
           section, it will send that password automatically to the server.

        2. Sets usermode 'Q' (no forwarding), to prevent the bot from being
           forwarded to a different channel than the one it was told to join.
           Bots almost never want to be in a channel they didn't specify.
    """

    @Module.handle("WELCOME")
    def set_no_forwarding(self, client, *args):
        client.send("MODE", client.user.nick, "+Q")

    @Module.handle("PASSWORD")
    def nickserv_password(self, client, *args):
        # Freenode will pass through the server password to NickServ,
        # skipping the need to send a password via PRIVMSG. If the bot
        # has an account under a different name than its nick, you can
        # use accountnick:password as the value of the password field.
        if self.controller.config.has_option("nickserv", "password"):
            # Sending directly via the socket, to avoid logging
            password = self.controller.config.get("nickserv", "password")
            _log.info("Sending NickServ password...")
            client.socket.send("PASS %s\r\n" % password)


module = FreenodeModule

# vim: set ts=4 sts=4 sw=4 et:

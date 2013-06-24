import logging

from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class FooneticModule(Module):
    """A KitnIRC module which provides Foonetic-specific functionality.

    Foonetic is irc.foonetic.net and runs UnrealIRCd. Functionality provided
    by this module:

        1. Sets the +B flag (bot).
        2. If the configuration has a 'password' field in the [nickserv]
           section, it will send that password automatically to the server.
    """

    @Module.handle("WELCOME")
    def set_bot_mode(self, client, *args):
        client.send("MODE", client.user.nick, "+B")

    @Module.handle("PASSWORD")
    def nickserv_password(self, client, *args):
        # Foonetic will pass through the server password to NickServ,
        # skipping the need to send a password via PRIVMSG.
        if self.controller.config.has_option("nickserv", "password"):
            # Sending directly via the socket, to avoid logging
            password = self.controller.config.get("nickserv", "password")
            _log.info("Sending NickServ password...")
            client.socket.send("PASS %s\r\n" % password)


module = FooneticModule

# vim: set ts=4 sts=4 sw=4 et:

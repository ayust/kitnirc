import logging

from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class BananasModule(Module):
    """A basic KitnIRC module which registers commands."""

    def add_command(self, client, command, event, helptext=None):
        self.trigger_event("ADDCOMMAND", client, [command, event, helptext])

    def remove_command(self, client, command, event):
        self.trigger_event("REMOVECOMMAND", client, [command, event])

    @Module.handle("COMMANDS")
    def register_commands(self, client, *args):
        _log.info("Registering commands...")
        self.add_command(client, "bananas", "BANANAS", "Go bananas!")

    def unregister_commands(self, client):
        self.remove_command(client, "bananas", "BANANAS")

    def start(self, *args, **kwargs):
        super(BananasModule, self).start(*args, **kwargs)
        self.register_commands(self.controller.client)

    def stop(self, *args, **kwargs):
        super(BananasModule, self).stop(*args, **kwargs)
        self.unreigster_commands(self.controller.client)

    @Module.handle("BANANAS")
    def bananas(self, client, actor, recipient, *args):
        client.reply(recipient, actor, "Banana banana banana.")
        return True


module = BananasModule

# vim: set ts=4 sts=4 sw=4 et:

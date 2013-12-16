import logging

from kitnirc.modular import Module


_log = logging.getLogger(__name__)


class AutoJoinModule(Module):
    """A KitnIRC module which auto-joins a set of channels on connect.

    The list of channels is read from the [channels] section in the config
    file. Items in this section can be either plain channel names or pairs
    of the form channelname=key for channels which require keys to join.

    Channels that would normally begin with # should omit that leading # in
    the config file, since that would cause the line to be considered a
    comment and ignored. Channel names will automatically have a # prefix
    added if they don't begin with a different valid channel prefix.

    If you need to, you can also specify channel names with a leading
    apostrophe - this apostrophe will be stripped off and the rest of
    the name left as-is with no modification. This can be useful in
    cases not covered by the above, e.g. channels that start with '##'.

    Example configuration section:

    [channels]
    foo
    bar=baz
    '##qux

    (This would join the #foo channel with no key, the #bar channel with
    a key of 'baz', and the ##qux channel with no key.)
    """

    @Module.handle("WELCOME")
    def join_channels(self, client, hostmask):
        config = self.controller.config

        if not config.has_section("channels"):
            _log.warning("No [channels] config section; not joining channels.")
            return

        chantypes = client.server.features.get("CHANTYPES", "#")
        count = 0

        _log.info("Beginning automatic channel joins...")

        for channel, key in config.items("channels"):
            # We add a # prefix to any channel that doesn't start with a
            # recognized channel type because in our config files, a #
            # would indicate a comment. Thus #-channels should just be
            # specified by leaving off the # in their name.
            if len(channel) > 1 and channel[0] == "'":
                channel = channel[1:]
            elif channel[0] not in chantypes:
                channel = "#" + channel
            client.join(channel, key)
            count += 1

        _log.info("Auto-join to %d channel(s) complete.", count)


module = AutoJoinModule


# vim: set ts=4 sts=4 sw=4 et:

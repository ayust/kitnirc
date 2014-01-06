def split_hostmask(hostmask):
    """Splits a nick@host string into nick and host."""
    nick, _, host = hostmask.partition('@')
    nick, _, user = nick.partition('!')
    return nick, user or None, host or None


class User(object):
    """A user on an IRC network."""

    def __init__(self, hostmask):
        self._nick = None
        self.update_from_hostmask(hostmask)
        self.realname = None
        self.modes = set()

    def update_from_hostmask(self, hostmask):
        self.nick, self.username, self.host = split_hostmask(hostmask)

    def _get_nick(self):
        return self._nick

    def _set_nick(self, value):
        if value.startswith("~"):
            self.ident = True
            self._nick = value[1:]
        else:
            self.ident = False
            self._nick = value

    nick = property(_get_nick, _set_nick)

    def __eq__(self, value):
        if isinstance(value, User):
            if value.host is None or self.host is None:
                return value.nick.lower() == self.nick.lower()
            return (value.nick.lower() == self.nick.lower() and
                    value.host.lower() == self.host.lower())
        elif isinstance(value, str):
            user = User(value)
            if "@" not in value:
                return user.nick.lower() == self.nick.lower()
            return (user.nick.lower() == self.nick.lower() and
                    user.host.lower() == self.host.lower())
        else:
            raise TypeError("Cannot compare User and %s" % type(value))

    def __str__(self):
        if not self.host:
            return self.nick
        if not self.username:
            return "%s@%s" % (self.nick, self.host)
        return "%s!%s@%s" % (self.nick, self.username, self.host)

    def __repr__(self):
        return "kitnirc.user.User(%s)" % str(self)

# vim: set ts=4 sts=4 sw=4 et:

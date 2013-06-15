def split_hostmask(hostmask):
    """Splits a nick@host string into nick and host."""
    nick, _, host = hostmask.partition('@')
    nick, _, user = nick.partition('!')
    return nick, user, host


class User(object):
    """A user on an IRC network."""

    def __init__(self, hostmask):
        self._nick = None
        self.nick, self.username, self.host = split_hostmask(hostmask)
        self.realname = None

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
            return value.nick == self.nick and value.host == self.host
        elif isinstance(value, str):
            user = User(value)
            if "@" not in value:
                return user.nick == self.nick
            return user.nick == self.nick and user.host == self.host
        else:
            raise TypeError("Cannot compare User and %s" % type(value))

# vim: set ts=4 sts=4 sw=4 et:

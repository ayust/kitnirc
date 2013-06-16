import logging
import re
import socket

from kitnirc.events import NUMERIC_EVENTS
from kitnirc.user import User

_log = logging.getLogger(__name__)




class Channel(object):
    """Information about an IRC channel.

    This class keeps track of things like who is in a channel, the channel
    topic, modes, and so on.
    """

    def __init__(self, name):
        self.name = name
        self.topic = None
        self.members = {}
        self.modes = {}

    def add_user(self, user):
        """Adds a user to the channel."""
        if not isinstance(user, User):
            user = User(user)
        if user.nick in self.members:
            _log.warning("Ignoring request to add user '%s' to channel '%s' "
                         "because that user is already in the member list.",
                         user, self.name)
            return
        self.members[user.nick] = user
        _log.debug("Added '%s' to channel '%s'", user, self.name)

    def remove_user(self, user):
        """Removes a user from the channel."""
        if not isinstance(user, User):
            user = User(user)
        if user.nick not in self.members:
            _log.warning("Ignoring request to remove user '%s' from channel "
                         "'%s' because that user is already in the member "
                         "list.", user, self.name)
            return
        del self.members[user.nick]
        _log.debug("Removed '%s' from channel '%s'", user, self.name)


class Host(object):
    """Information about an IRC server.

    This class keeps track of things like what channels a client is in,
    who is in those channels, and other such details.
    """

    def __init__(self, host, port):
        self.host = host
        # We also keep track of the host we originally connected to - e.g.
        # if we connected to a round-robin alias.
        self.original_host = host
        self.port = port
        self.password = None

        self.motd = "" # The full text of the MOTD, once received
        self._motd = [] # Receive buffer; do not use for reading

        # The channels we're in, keyed by channel name
        self.channels = {}

        # What features modes are available on the server
        self.features = dict()
        self.user_modes = set()
        self.channel_modes = set()

        # Miscellaneous information about the server
        self.version = None
        self.created = None

    def add_channel(self, name):
        if name in self.channels:
            _log.warning("Ignoring request to add a channel that has already "
                         "been added: '%s'", name)
            return
        self.channels[name] = Channel(name)
        _log.info("Entered channel %s.", name)

    def remove_channel(self, name):
        if name not in self.channels:
            _log.warning("Ignoring request to remove a channel that hasn't "
                         "been added: '%s'", name)
            return
        del self.channels[name]
        _log.info("Left channel %s.", name)


class Client(object):
    """An IRC client.

    This class wraps a connection to a single IRC network and provides
    additional functionality (e.g. tracking of nicks and channels).
    """

    def __init__(self, host, port=6667):
        self.server = Host(host, port)
        self.connected = False
        self.socket = None
        self._stop = False
        self._buffer = ""

        # Queues for event dispatching.
        self.event_handlers = {

            ###### CONNECTION-LEVEL EVENTS ######

            # Fires after the client's socket connects.
            'CONNECTED': [on_connect],
            # Fires every time a line is received
            'LINE': [on_line],
            # Fires whenever a line isn't handled by LINE
            'RAWLINE': [],

            ###### IRC-LEVEL EVENTS ######

            # Fires when receiving the 001 RPL_WELCOME message upon
            # being recognized as a valid user by the IRC server.
            'WELCOME': [],
            # Fires when a privmsg is received
            'PRIVMSG': [], # actor, recipien
            # Fires when a notice is received
            'NOTICE': [],
            # Fires when a complete MOTD is received
            'MOTD': [],
            # Fires when a user joins a channel
            'JOIN': [],
            # Fires when a user parts a channel
            'PART': [],
            # Fires when a user is kicked from a channel
            'KICK': [],
            # Fires when the list of users in a channel has been updated
            'MEMBERS': [],
            # Fires whenever a mode change occurs
            'MODE': [],
        }

    def add_handler(self, event, handler):
        """Adds a handler for a particular event.

        Handlers are appended to the list, so a handler added earlier
        will be called before a handler added later. If you wish to
        insert a handler at another position, you should modify the
        event_handlers property directly:

            my_client.event_handlers['PRIVMSG'].insert(0, my_handler)
        """
        if event not in self.event_handlers:
            _log.warning("Adding event handler for unknown event %s.")
            self.event_handlers[event] = [handler]
        else:
            self.event_handlers[event].append(handler)

    def dispatch_event(self, event, *args):
        """Dispatches an event.

        Returns a boolean indicating whether or not a handler
        suppressed further handling of the event (even the last).
        """
        if event not in self.event_handlers:
            _log.error("Dispatch requested for unknown event '%s'", event)
            return False
        elif event != "LINE":
            _log.debug("Dispatching event %s %r", event, args)

        try:
            for handler in self.event_handlers[event]:
                # (client, server, *args) : args are dependent on event
                if handler(self, *args):
                    # Returning a truthy value supresses further handlers
                    # for this event.
                    return True
        except Exception:
            _log.exception("Error while processing event '%s'", event)

        # Fall back to the RAWLINE event if LINE can't process it.
        if event == 'LINE':
            return self.dispatch_event('RAWLINE', *args)

        return False

    def connect(self, nick, username=None, realname=None, password=None):
        """Connect to the server using the specified credentials."""
        self.user = User(nick)
        self.user.username = username or nick
        self.user.realname = realname or username or nick

        _log.info("Connecting to %s as %s ...", self.server.host, nick)

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((self.server.host, self.server.port))
        self.connected = True

        _log.info("Connected to %s.", self.server.host)

        if password:
            # We bypass our own send() function here to avoid logging passwords
            _log.info("Sending server password.")
            self.socket.send("PASS %s\r\n" % password)
            self.server.password = password

        self.dispatch_event('CONNECTED')

    def disconnect(self, msg="Shutting down..."):
        if not self.connected:
            _log.warning("Disconnect requested from non-connected client (%s)",
                self.server.host)
            return

        _log.info("Disconnecting from %s ...", self.server.host)
        self._stop = True
        self.send("QUIT", ":" + msg)
        try:
            self.socket.close()
        except socket.error:
            pass

    def run(self):
        """Process events such as incoming data.

        This method blocks indefinitely. It will only return after the
        connection to the server is closed.
        """
        self._stop = False # Allow re-starting the event loop
        while not self._stop:
            try:
                self._buffer += self.socket.recv(4096)
            except socket.error:
                raise

            lines = self._buffer.split("\n")
            self._buffer = lines.pop() # We may still need to more of the last
            for line in lines:
                line = line.rstrip("\r")
                _log.debug("%s --> %s", self.server.host, line)
                self.dispatch_event("LINE", line)

    def send(self, *args):
        """Sends a single raw message to the IRC server.

        Arguments are automatically joined by spaces. No newlines are allowed.
        """
        msg = " ".join(args)
        if "\n" in msg:
            raise ValueError("Cannot send() a newline. Args: %r" % args)
        _log.debug("%s <-- %s", self.server.host, msg)
        self.socket.send(msg + "\r\n")

    def nick(self, nick):
        """Attempt to set the nickname for this connection."""
        _log.info("Requesting nick change to '%s'", nick)
        self.send("NICK", nick)

    def userinfo(self, username, realname=None):
        """Set the username and realname for this connection.

        Note: this should only be called once, on connect. (The default
        on-connect routine calls this automatically.)
        """
        realname = realname or username

        _log.info("Requesting user info update: username=%s realname=%s",
            username, realname)

        self.send("USER", username, socket.getfqdn(), self.server.host,
            ":%s" % realname) # Realname should always be prefixed by a colon
        self.user.username = username
        self.user.realname = realname

    def msg(self, target, message):
        """Send a message to a user or channel."""
        self.send("PRIVMSG", target, ":" + message)

    def notice(self, target, message):
        """Send a NOTICE to a user or channel."""
        self.send("NOTICE", target, ":" + message)

    def join(self, target, key=None):
        """Attempt to join a channel.

        The optional second argument is the channel key, if needed.
        """
        chantypes = self.server.features.get("CHANTYPES", "#")
        if not target or target[0] not in chantypes:
            # Among other things, this prevents accidentally sending the
            # "JOIN 0" command which actually removes you from all channels
            _log.warning("Refusing to join channel that does not start "
                         "with one of '%s': %s", chantypes, target)
            return

        if target in self.server.channels:
            _log.warning("Ignoring request to join channel '%s' because we "
                         "are already in that channel.", target)
            return

        _log.info("Joining channel %s ...", target)
        self.send("JOIN", target, *([key] if key else []))

    def part(self, target, message=None):
        """Part a channel."""
        if target not in self.server.channels:
            _log.warning("Ignoring request to part channel '%s' because we "
                         "are not in that channel.", target)
            return
        self.send("PART", target, *([message] if message else []))

    def handle(self, event):
        """Decorator for adding a handler function for a particular event.

        Usage:

            my_client = Client()

            @my_client.handle("WELCOME")
            def welcome_handler(client, *params):
                # Do something with the event.
                pass
        """
        def dec(func):
            self.add_handler(event, func)
            return func
        return dec

    def _get_prefixes(self):
        """Get the possible nick prefixes and associated modes for a client."""
        prefixes = {
            "@": "o",
            "+": "v",
        }
        feature_prefixes = self.server.features.get('PREFIX')
        if feature_prefixes:
            modes = feature_prefixes[1:len(feature_prefixes)//2]
            symbols = feature_prefixes[len(feature_prefixes)//2+1:]
            prefixes = dict(zip(symbols, modes))
        return prefixes


################################################################################
# DEFAULT LOW-LEVEL EVENT HANDLERS
################################################################################
def on_connect(client):
    """Default on-connect actions."""
    client.nick(client.user.nick)
    client.userinfo(client.user.username, client.user.realname)


def on_line(client, line):
    """Default handling for incoming lines.

    This handler will automatically manage the following IRC messages:

      PING:
        Responds with a PONG.
      PRIVMSG:
        Dispatches the PRIVMSG event.
      NOTICE:
        Dispatches the NOTICE event.
      MOTDSTART:
        Initializes MOTD receive buffer.
      MOTD:
        Appends a line to the MOTD receive buffer.
      ENDOFMOTD:
        Joins the contents of the MOTD receive buffer, assigns the result
        to the .motd of the server, and dispatches the MOTD event.
    """
    if line.startswith("PING"):
        client.send("PONG" + line[4:])
        return True

    if line.startswith(":"):
        actor, _, line = line[1:].partition(" ")
    else:
        actor = None
    command, _, args = line.partition(" ")
    command = NUMERIC_EVENTS.get(command, command)

    parser = PARSERS.get(command, False)
    if parser:
        parser(client, command, actor, args)
        return True
    elif parser is False:
        # Explicitly ignored message
        return True


################################################################################
# COMMAND PARSERS
################################################################################

# Holds a mapping of IRC commands to functions that will parse them and
# take any necessary action. We define some ignored events here as well.
PARSERS = {
    "YOURHOST": False,
}


def parser(*events):
    """Decorator for convenience - adds a function as a parser for event(s)."""
    def dec(func):
        for event in events:
            PARSERS[event] = func
        return func
    return dec


@parser("PRIVMSG", "NOTICE")
def _parse_msg(client, command, actor, args):
    """Parse a PRIVMSG or NOTICE and dispatch the corresponding event."""
    recipient, _, message = args.partition(' :')
    recipient = User(recipient)
    client.dispatch_event(command, actor, recipient, message)


@parser("MOTDSTART", "ENDOFMOTD", "MOTD")
def _parse_motd(client, command, actor, args):
    if command == "MOTDSTART":
        client.server._motd = []
    if command == "ENDOFMOTD":
        client.server.motd = "\n".join(client.server._motd)
        client.dispatch_event("MOTD", client.server.motd)
    if command == "MOTD":  # MOTD line
        client.server._motd.append(args.partition(":")[2])


@parser("JOIN")
def _parse_join(client, command, actor, args):
    """Parse a JOIN and update channel states, then dispatch events.

    Note that two events are dispatched here:
        - JOIN, because a user joined the channel
        - MEMBERS, because the channel's members changed
    """
    actor = User(actor)
    channel = args.lstrip(' :')
    if actor.nick == client.user.nick:
        client.server.add_channel(channel)
        client.user.host = actor.host # now we know our host per the server
    client.server.channels[channel].add_user(actor)
    client.dispatch_event("JOIN", actor, channel)
    if actor.nick != client.user.nick:
        # If this is us joining, the namreply will trigger this instead
        client.dispatch_event("MEMBERS", channel)


@parser("PART")
def _parse_part(client, command, actor, args):
    """Parse a PART and update channel states, then dispatch events.

    Note that two events are dispatched here:
        - PART, because a user parted the channel
        - MEMBERS, because the channel's members changed
    """
    actor = User(actor)
    channel, _, message = args.partition(' :')
    client.server.channels[channel].remove_user(actor)
    if actor.nick == client.user.nick:
        client.server.remove_channel(channel)
    client.dispatch_event("PART", actor, channel, message)
    client.dispatch_event("MEMBERS", channel)


@parser("QUIT")
def _parse_quit(client, command, actor, args):
    """Parse a QUIT and update channel states, then dispatch events.

    Note that two events are dispatched here:
        - QUIT, because a user quit the server
        - MEMBERS, for each channel the user is no longer in
    """
    actor = User(actor)
    _, _, message = args.partition(':')
    client.dispatch_event("QUIT", actor, message)
    for chan in client.server.channels.itervalues():
        if actor.nick in chan.members:
            chan.remove(actor)
            client.dispatch_event("MEMBERS", chan.name)


@parser("KICK")
def _parse_kick(client, command, actor, args):
    """Parse a KICK and update channel states, then dispatch events.

    Note that two events are dispatched here:
        - KICK, because a user was kicked from the channel
        - MEMBERS, because the channel's members changed
    """
    actor = User(actor)
    args, _, message = args.partition(' :')
    channel, target = args.split()
    target = User(target)
    client.server.channels[channel].remove_user(target)
    if target.nic == client.user.nick:
        client.server.remove_channel(channel)
    client.dispatch_event("KICK", actor, target, channel, message)
    client.dispatch_event("MEMBERS", channel)


@parser("TOPIC")
def _parse_topic(client, command, actor, args):
    """Parse a TOPIC and update channel state, then dispatch a TOPIC event."""
    channel, _, topic = args.partition(" :")
    client.server.channels[channel].topic = topic or None
    if actor:
        actor = User(actor)
    client.dispatch_event("TOPIC", actor, channel, topic)


@parser("WELCOME")
def _parse_welcome(client, command, actor, args):
    """Parse a WELCOME and update user state, then dispatch a WELCOME event."""
    _, _, hostmask = args.rpartition(' ')
    client.user.update_from_hostmask(hostmask)
    client.dispatch_event("WELCOME", hostmask)


@parser("CREATED")
def _parse_created(client, command, actor, args):
    """Parse CREATED and update the Host object."""
    m = re.search("This server was created (.+)$", args)
    if m:
        client.server.created = m.group(1)


@parser("MYINFO")
def _parse_myinfo(client, command, actor, args):
    """Parse MYINFO and update the Host object."""
    _, server, version, usermodes, channelmodes = args.split(None, 5)[:5]
    s = client.server
    s.host = server
    s.version = version
    s.user_modes = set(usermodes)
    s.channel_modes = set(channelmodes)


@parser("FEATURELIST")
def _parse_featurelist(client, command, actor, args):
    """Parse FEATURELIST and update the Host object."""
    # Strip off ":are supported by this server"
    args = args.rsplit(":", 1)[0]
    # Strip off the nick; we know it's addressed to us.
    _, _, args = args.partition(' ')

    items = args.split()
    for item in items:
        feature, _, value = item.partition("=")

        # Convert integer values to actual integers for convenience
        try:
            value = int(value)
        except (ValueError, TypeError):
            pass

        client.server.features[feature] = value


@parser("NAMREPLY")
def _parse_namreply(client, command, actor, args):
    """Parse NAMREPLY and update a Channel object."""
    prefixes = client._get_prefixes()

    channelinfo, _, useritems = args.partition(' :')
    _, _, channel = channelinfo.rpartition(' ')  # channeltype channelname

    c = client.server.channels.get(channel)
    if not c:
        _log.warning("Ignoring NAMREPLY for channel '%s' which we are not in.",
            channel)
        return

    # We bypass Channel.add_user() here because we just want to sync in any
    # users we don't already have, regardless of if other users exist, and
    # we don't want the warning spam.
    for nick in useritems.split():
        modes = set()
        while nick[0] in prefixes:
            modes.add(prefixes[nick[0]])
            nick = nick[1:]
        user = c.members.get(nick)
        if not user:
            user = c.members[nick] = User(nick)
            _log.debug("Added user %s to channel %s", user, channel)
        user.modes |= modes


@parser("ENDOFNAMES")
def _parse_endofnames(client, command, actor, args):
    """Parse an ENDOFNAMES and dispatch a NAMES event for the channel."""
    args = args.split(" :", 1)[0] # Strip off human-readable message
    _, _, channel = args.rpartition(' ')
    client.dispatch_event('MEMBERS', channel)


@parser("MODE")
def _parse_mode(client, command, actor, args):
    """Parse a mode changes, update states, and dispatch MODE events."""
    chantypes = client.server.features.get("CHANTYPES", "#")
    channel, _, args = args.partition(" ")
    args = args.lstrip(":")

    if channel[0] not in chantypes:
        # Personal modes
        for modes in args.split():
            op, modes = modes[0], modes[1:]
            for mode in modes:
                if op == "+":
                    client.user.modes.add(mode)
                else:
                    client.user.modes.discard(mode)
                client.dispatch_event("MODE", actor, channel, op, mode, None)
        return

    # channel-specific modes
    chan = client.server.channels[channel]

    user_modes = set(client._get_prefixes().itervalues())

    chanmodes = client.server.features.get('CHANMODES')
    if not chanmodes:
        # Defaults from RFC 2811
        argument_modes = set("beIkl")
        set_arg_modes = set("kl")
        toggle_modes = set("aimnqpsrt")
    else:
        chanmodes = chanmodes.split(",")
        list_modes = set(chanmodes[0])
        always_arg_modes = set(chanmodes[1])
        set_arg_modes = set(chanmodes[2])
        toggle_modes = set(chanmodes[3])
        argument_modes = list_modes | always_arg_modes | set_arg_modes

    tokens = args.split()
    while tokens:
        modes, tokens = tokens[0], tokens[1:]
        op, modes = modes[0], modes[1:]

        for mode in modes:
            argument = None
            if mode in (user_modes | argument_modes):
                argument, tokens = tokens[0], tokens[1:]

            if mode in user_modes:
                user = client.server.channels[channel].members[argument]
                if op == "+":
                    user.modes.add(mode)
                else:
                    user.modes.discard(mode)

            if op == "+":
                if mode in (always_arg_modes | set_arg_modes):
                    chan.modes[mode] = argument
                elif mode in toggle_modes:
                    chan.modes[mode] = True
            else:
                if mode in (always_arg_modes | set_arg_modes | toggle_modes):
                    if mode in chan.modes:
                        del chan.modes[mode]

            # list-type modes (bans+exceptions, invite masks) aren't stored,
            # but do generate MODE events.
            client.dispatch_event("MODE", actor, channel, op, mode, argument)

# vim: set ts=4 sts=4 sw=4 et:

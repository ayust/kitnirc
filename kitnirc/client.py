import logging
import re
import socket

try:
    import ssl as _ssl
    _hush_pyflakes = [_ssl]
    del _hush_pyflakes
except ImportError:
    _ssl = None # No SSL support

from kitnirc.events import NUMERIC_EVENTS
from kitnirc.user import User

_log = logging.getLogger(__name__)


class Channel(object):
    """Information about an IRC channel.

    This class keeps track of things like who is in a channel, the channel
    topic, modes, and so on.
    """

    def __init__(self, name):
        self.name = name.lower()
        self.topic = None
        self.members = {}
        self.modes = {}

    def __str__(self):
        return self.name

    def __repr__(self):
        return "kitnirc.client.Channel(%r)" % self.name

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
                         "'%s' because that user is already not in the member "
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

        # Buffer for information from WHOIS response lines
        self._whois = {}

        # The channels we're in, keyed by channel name
        self.channels = {}

        # What features modes are available on the server
        self.features = dict()
        self.user_modes = set()
        self.channel_modes = set()

        # Miscellaneous information about the server
        self.version = None
        self.created = None

    def __str__(self):
        return self.host

    def __repr__(self):
        return "kitnirc.client.Host(%r, %r)" % (self.host, self.port)

    def add_channel(self, channel):
        if not isinstance(channel, Channel):
            channel = Channel(channel)
        if channel.name in self.channels:
            _log.warning("Ignoring request to add a channel that has already "
                         "been added: '%s'", channel)
            return
        self.channels[channel.name] = channel
        _log.info("Entered channel %s.", channel)

    def remove_channel(self, channel):
        if isinstance(channel, Channel):
            channel = channel.name
        channel = channel.lower()
        if channel not in self.channels:
            _log.warning("Ignoring request to remove a channel that hasn't "
                         "been added: '%s'", channel)
            return
        del self.channels[channel]
        _log.info("Left channel %s.", channel)

    def get_channel(self, channel):
        if isinstance(channel, Channel):
            channel = channel.name
        channel = channel.lower()
        if channel not in self.channels:
            _log.warning("Ignoring request to get a channel that hasn't "
                         "been added: '%s'", channel)
            return None
        return self.channels[channel]

    def in_channel(self, channel):
        channel = str(channel).lower()
        return channel in self.channels


class Client(object):
    """An IRC client.

    This class wraps a connection to a single IRC network and provides
    additional functionality (e.g. tracking of nicks and channels).
    """

    def __init__(self, host=None, port=6667):
        if host:
            self.server = Host(host, port)
        else:
            self.server = None
        self.connected = False
        self.socket = None
        self._stop = False
        self._buffer = ""

        # Queues for event dispatching.
        self.event_handlers = {

            ###### CONNECTION-LEVEL EVENTS ######

            # Fires while the client is connecting, when a password should be
            # supplied. If nothing supplies a password, the password argument
            # of connect() will be used (if set).
            "PASSWORD": [],
            # Fires after the client's socket connects.
            "CONNECTED": [on_connect],
            # Fires every time a line is received
            "LINE": [on_line],
            # Fires whenever a line isn't handled by LINE
            "RAWLINE": [],
            # Fires whenever we see incoming network activity
            "ACTIVITY": [],

            ###### IRC-LEVEL EVENTS ######

            # Fires when receiving the 001 RPL_WELCOME message upon
            # being recognized as a valid user by the IRC server.
            "WELCOME": [],
            # Fires when a privmsg is received
            "PRIVMSG": [], # actor, recipient
            # Fires when a notice is received
            "NOTICE": [],
            # Fires when a complete MOTD is received
            "MOTD": [],
            # Fires when a user joins a channel
            "JOIN": [],
            # Fires when a user parts a channel
            "PART": [],
            # Fires when a user quits the server
            "QUIT": [],
            # Fires when a user is kicked from a channel
            "KICK": [],
            # Fires when the list of users in a channel has been updated
            "MEMBERS": [],
            # Fires whenever a mode change occurs
            "MODE": [],
            # Fires when a WHOIS response is complete
            "WHOIS": [],
            # Fires when a channel topic changes
            "TOPIC": [],
            # Fires when someone invites us to a channel
            "INVITE": [],
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
            _log.info("Adding event handler for new event %s.", event)
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
        except Exception as e:
            _log.exception("Error while processing event '%s': %r", event, e)

        # Fall back to the RAWLINE event if LINE can't process it.
        if event == "LINE":
            return self.dispatch_event("RAWLINE", *args)

        return False

    def connect(self, nick, username=None, realname=None, password=None,
                host=None, port=6667, ssl=None):
        """Connect to the server using the specified credentials.

        Note: if host is specified here, both the host and port arguments
        passed to Client.__init__ will be ignored.

        If the 'ssl' argument is boolean true, will use SSL. If it is a
        dictionary, will both use SSL and pass the contents as kwargs to
        the ssl.wrap_socket() call.
        """
        if host:
            self.server = Host(host, port)
        if self.server is None:
            _log.error("Can't connect() without a host specified.")
            return
        self.user = User(nick)
        self.user.username = username or nick
        self.user.realname = realname or username or nick

        _log.info("Connecting to %s as %s ...", self.server.host, nick)

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if ssl and _ssl:
            ssl_kwargs = ssl if isinstance(ssl, dict) else {}
            self.socket = _ssl.wrap_socket(self.socket, **ssl_kwargs)
        elif ssl:
            _log.error("SSL requested but no SSL support available!")
            return

        self.socket.connect((self.server.host, self.server.port))
        self.connected = True

        _log.info("Connected to %s.", self.server.host)

        # Allow an event handler to supply a password instead, if it wants
        suppress_password = self.dispatch_event("PASSWORD")

        if password and not suppress_password:
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
            self._buffer = lines.pop() # Last line may not have been fully read
            for line in lines:
                line = line.rstrip("\r")
                _log.debug("%s --> %s", self.server.host, line)
                self.dispatch_event("LINE", line)
                self.dispatch_event("ACTIVITY")

    def ping(self):
        "Convenience method to send a PING to server"
        self.send("PING " + self.server.host)

    def send(self, *args):
        """Sends a single raw message to the IRC server.

        Arguments are automatically joined by spaces. No newlines are allowed.
        """
        msg = " ".join(a.nick if isinstance(a, User) else str(a) for a in args)
        if "\n" in msg:
            raise ValueError("Cannot send() a newline. Args: %s" % repr(args))
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

    def reply(self, incoming, user, message):
        """Replies to a user in a given channel or PM.

        If the specified incoming is a user, simply sends a PM to user.
        If the specified incoming is a channel, prefixes the message with the
        user's nick and sends it to the channel.

        This is specifically useful in creating responses to commands that can
        be used in either a channel or in a PM, and responding to the person
        who invoked the command.
        """
        if not isinstance(user, User):
            user = User(user)
        if isinstance(incoming, User):
            self.msg(user, message)
        else:
            self.msg(incoming, "%s: %s" % (user.nick, message))

    def notice(self, target, message):
        """Send a NOTICE to a user or channel."""
        self.send("NOTICE", target, ":" + message)

    def topic(self, target, message):
        """Sets TOPIC for a channel."""
        self.send("TOPIC", target, ":" + message)

    def ctcp(self, target, message):
        """Send a CTCP message to a user or channel."""
        self.msg(target, "\x01%s\x01" % message)

    def emote(self, target, message):
        """Sends an emote (/me ...) to a user or channel."""
        self.ctcp(target, "ACTION %s" % message)

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
            return False

        if self.server.in_channel(target):
            _log.warning("Ignoring request to join channel '%s' because we "
                         "are already in that channel.", target)
            return False

        _log.info("Joining channel %s ...", target)
        self.send("JOIN", target, *([key] if key else []))
        return True

    def invite(self, channel, nick):
        """Attempt to invite a user to a channel."""
        self.send("INVITE", nick, channel)

    def part(self, target, message=None):
        """Part a channel."""
        if not self.server.in_channel(target):
            _log.warning("Ignoring request to part channel '%s' because we "
                         "are not in that channel.", target)
            return
            return False
        self.send("PART", target, *([message] if message else []))
        return True

    def quit(self, message=None):
        """Quit the server (and stop the event loop).

        This actually just calls .disconnect() with the provided message."""
        self.disconnect(message or "Bye")

    def kick(self, channel, nick, message=None):
        """Attempt to kick a user from a channel.

        If a message is not provided, defaults to own nick.
        """
        self.send("KICK", channel, nick, ":%s" % (message or self.user.nick))

    def whois(self, nick):
        """Request WHOIS information about a user."""
        self.send("WHOIS", nick)

    def mode(self, channel, add='', remove=''):
        """Add and/or remove modes for a given channel.

        The 'add' and 'remove' arguments may, if specified, be either
        sequences or dictionaries. If a dictionary is specified, the
        corresponding values will be passed as arguments (with expansion
        if necessary - {'b': ['foo','bar']} will result in two bans:
            MODE <channel> +bb foo bar

        (Values for modes which do not take arguments are ignored.)
        """
        if not self.server.in_channel(channel):
            _log.warning("Ignoring request to set modes in channel '%s' "
                         "because we are not in that channel.", channel)
            return

        chanmodes = self._get_chanmodes()
        list_modes, always_arg_modes, set_arg_modes, toggle_modes = chanmodes
        # User privilege levels are not always included in channel modes list
        always_arg_modes |= set(self._get_prefixes().itervalues())

        def _arg_to_list(arg, argument_modes, toggle_modes):
            if not isinstance(arg, dict):
                modes = set(arg)
                invalid_modes = modes - toggle_modes
                if invalid_modes:
                    _log.warning("Ignoring the mode(s) '%s' because they are "
                                 "missing required arguments.",
                                 "".join(invalid_modes))
                return modes & toggle_modes, []

            # Okay, so arg is a dict
            modes_with_args = []
            modes_without_args = set()
            for k,v in arg.iteritems():
                if isinstance(v, str):
                    v = [v]
                if k in argument_modes:
                    for val in v:
                        modes_with_args.append((k,val))
                elif k in toggle_modes:
                    modes_without_args.add(k)
                else:
                    _log.warning("Ignoring request to set channel mode '%s' "
                                 "because it is not a recognized mode.", k)
            return modes_without_args, modes_with_args

        add_modes, add_modes_args = _arg_to_list(
            add, list_modes | always_arg_modes | set_arg_modes, toggle_modes)
        remove_modes, remove_modes_args = _arg_to_list(
            remove, list_modes | always_arg_modes, set_arg_modes | toggle_modes)

        max_arg = self.server.features.get("MODES") or 3

        def _send_modes(op, toggle_modes, arg_modes):
            while toggle_modes or arg_modes:
                modes = "".join(toggle_modes)
                toggle_modes = ""
                now_modes, arg_modes = arg_modes[:max_arg], arg_modes[max_arg:]
                modes += "".join(mode for mode,arg in now_modes)
                modes += "".join(" %s" % arg for mode,arg in now_modes)
                self.send("MODE", channel, "%s%s" % (op, modes))

        _send_modes("+", add_modes, add_modes_args)
        _send_modes("-", remove_modes, remove_modes_args)


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

    def _get_chanmodes(self):
        chanmodes = self.server.features.get('CHANMODES')
        if not chanmodes:
            # Defaults from RFC 2811
            list_modes = set("beI")
            always_arg_modes = set()
            set_arg_modes = set("kl")
            toggle_modes = set("aimnqpsrt")
        else:
            chanmodes = chanmodes.split(",")
            list_modes = set(chanmodes[0])
            always_arg_modes = set(chanmodes[1])
            set_arg_modes = set(chanmodes[2])
            toggle_modes = set(chanmodes[3])
        return list_modes, always_arg_modes, set_arg_modes, toggle_modes


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
    chantypes = client.server.features.get("CHANTYPES", "#")
    if recipient[0] in chantypes:
        recipient = client.server.get_channel(recipient) or recipient.lower()
    else:
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
    channel = args.lstrip(' :').lower()
    if actor.nick == client.user.nick:
        client.server.add_channel(channel)
        client.user.host = actor.host # now we know our host per the server
    channel = client.server.get_channel(channel)
    channel.add_user(actor)
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
    channel = client.server.get_channel(channel)
    channel.remove_user(actor)
    if actor.nick == client.user.nick:
        client.server.remove_channel(channel)
    client.dispatch_event("PART", actor, channel, message)
    if actor.nick != client.user.nick:
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
            chan.remove_user(actor)
            client.dispatch_event("MEMBERS", chan)


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
    channel = client.server.get_channel(channel)
    channel.remove_user(target)
    target = User(target)
    if target.nick == client.user.nick:
        client.server.remove_channel(channel)
    client.dispatch_event("KICK", actor, target, channel, message)
    client.dispatch_event("MEMBERS", channel)


@parser("TOPIC")
def _parse_topic(client, command, actor, args):
    """Parse a TOPIC and update channel state, then dispatch a TOPIC event."""
    channel, _, topic = args.partition(" :")
    channel = client.server.get_channel(channel)
    channel.topic = topic or None
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

    c = client.server.get_channel(channel)
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
    channel = client.server.get_channel(channel) or channel.lower()
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
                client.dispatch_event("MODE", actor, client.user, op, mode, None)
        return

    # channel-specific modes
    chan = client.server.get_channel(channel)

    user_modes = set(client._get_prefixes().itervalues())

    chanmodes = client._get_chanmodes()
    list_modes, always_arg_modes, set_arg_modes, toggle_modes = chanmodes
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
                user = client.server.get_channel(channel).members[argument]
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
            client.dispatch_event("MODE", actor, chan, op, mode, argument)


@parser("WHOISUSER", "WHOISCHANNELS", "WHOISIDLE", "WHOISSERVER",
        "WHOISOPERATOR", "WHOISACCOUNT", "WHOISBOT", "WHOISREGNICK",
        "ENDOFWHOIS")
def _parse_whois(client, command, actor, args):
    """Parse the content responses from a WHOIS query.

    Individual response lines are parsed and used to fill in data in a buffer,
    the full contents of which are then sent as the argument to the WHOIS
    event dispatched when an ENDOFWHOIS line is received from the server.
    """
    _, _, args = args.partition(" ") # Strip off recipient, we know it"s us
    nick, _, args = args.partition(" ")
    if client.server._whois.get("nick") != nick:
        client.server._whois = {"nick": nick}
    response = client.server._whois

    if command == "WHOISUSER":
        first, _, response["realname"] = args.partition(":")
        response["username"], response["host"] = first.split()[:2]
        return

    if command == "WHOISISSERVER":
        response["server"], _, response["serverinfo"] = args.partition(" :")
        return

    if command == "WHOISOPERATOR":
        response["oper"] = True
        return

    if command == "WHOISIDLE":
        response["idle"], _, _ = args.partition(" :")
        response["idle"] = int(response["idle"])
        return

    if command == "WHOISCHANNELS":
        modes = "".join(client._get_prefixes())
        print repr(modes)
        channels = args.lstrip(":").split()
        response["channels"] = dict(
            (chan.lstrip(modes), chan[0] if chan[0] in modes else "")
            for chan in channels)
        return

    if command == "WHOISACCOUNT":
        response["account"], _, _ = args.partition(" :")
        return

    if command == "WHOISBOT":
        response["bot"] = True
        return

    if command == "WHOISREGNICK":
        response["registered"] = True
        return

    if command == "ENDOFWHOIS":
        client.dispatch_event("WHOIS", response)


@parser("NICK")
def _parse_nick(client, command, actor, args):
    """Parse a NICK response, update state, and dispatch events.

    Note: this function dispatches both a NICK event and also one or more
    MEMBERS events for each channel the user that changed nick was in.
    """
    old_nick, _, _ = actor.partition('!')
    new_nick = args

    if old_nick == client.user.nick:
        client.user.nick = new_nick

    modified_channels = set()
    for channel in client.server.channels.itervalues():
        user = channel.members.get(old_nick)
        if user:
            user.nick = new_nick
            channel.members[new_nick] = user
            del channel.members[old_nick]
            modified_channels.add(channel.name)

    client.dispatch_event("NICK", old_nick, new_nick)
    for channel in modified_channels:
        client.dispatch_event("MEMBERS", channel)


@parser("INVITE")
def _parse_invite(client, command, actor, args):
    """Parse an INVITE and dispatch an event."""
    target, _, channel = args.rpartition(" ")
    client.dispatch_event("INVITE", actor, target, channel.lower())


@parser("NICKNAMEINUSE")
def _parse_nicknameinuse(client, command, actor, args):
    """Parse a NICKNAMEINUSE message and dispatch an event.

    The parameter passed along with the event is the nickname
    which is already in use.
    """
    nick, _, _ = args.rpartition(" ")
    client.dispatch_event("NICKNAMEINUSE", nick)

# vim: set ts=4 sts=4 sw=4 et:

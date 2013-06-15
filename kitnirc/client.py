import logging
import socket

from kitnirc.events import NUMERIC_EVENTS
from kitnirc.user import User

_log = logging.getLogger(__name__)


class Host(object):
    """Information about an IRC server.

    This class keeps track of things like what channels a client is in,
    who is in those channels, and other such details.
    """

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.user = None
        self.password = None
        self.motd = ""
        self._motd = [] # Receive buffer; do not use for reading


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
            'PRIVMSG': [],
            # Fires when a notice is received
            'NOTICE': [],
            # Fires when a complete MOTD is received
            'MOTD': [],
        }

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
        self.user = self.server.user = User(nick)

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
                _log.debug("%s --> %s", self.server.host, line)
                self.dispatch_event('LINE', line)

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
        self.send('NICK', nick)

    def userinfo(self, username, realname=None):
        """Set the username and realname for this connection.

        Note: this should only be called once, on connect. (The default
        on-connect routine calls this automatically.)
        """
        realname = realname or username

        _log.info("Requesting user info update: username=%s realname=%s",
            username, realname)

        self.send('USER', username, socket.getfqdn(), self.server.host,
            ":%s" % realname) # Realname should always be prefixed by a colon
        self.user.username = username
        self.user.realname = realname

    def msg(self, target, message):
        """Send a message to a user or channel."""
        self.send("PRIVMSG", target, ":" + message)

    def notice(self, target, message):
        """Send a NOTICE to a user or channel."""
        self.send("NOTICE", target, ":" + message)


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
        sender, _, line = line.partition(" ")
    else:
        sender = None
    command, _, args = line.partition(" ")
    command = NUMERIC_EVENTS.get(command, command)

    if command in PARSERS:
        PARSERS[command](client, command, sender, args)
        return True


################################################################################
# COMMAND PARSERS
################################################################################

# Holds a mapping of IRC commands to functions that will parse them and
# take any necessary action.
PARSERS = {}


def _parse_msg(client, command, sender, args):
    """Parse a PRIVMSG or NOTICE and dispatch the corresponding event."""
    recipient, _, message = args.partition(' :')
    recipient = User(recipient)
    client.dispatch_event(command, sender, recipient, message)
PARSERS['PRIVMSG'] = PARSERS['NOTICE'] = _parse_msg


def _parse_motd(client, command, sender, args):
    if command == "MOTDSTART":
        client.server._motd = []
    if command == "ENDOFMOTD":
        client.server.motd = "\n".join(client.server._motd)
        client.dispatch_event("MOTD", client.server.motd)
    if command == "MOTD":  # MOTD line
        client.server._motd.append(args.partition(":")[2])
PARSERS["MOTDSTART"] = PARSERS["ENDOFMOTD"] = PARSERS["MOTD"] = _parse_motd

# vim: set ts=4 sts=4 sw=4 et:

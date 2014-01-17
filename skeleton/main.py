#!/usr/bin/python
"""Skeleton bot using KitnIRC. Just connects to a server."""
import argparse
import logging
import os

import kitnirc.client
import kitnirc.modular

# Command-line arguments
parser = argparse.ArgumentParser(description="Example IRC client.")

parser.add_argument("host", nargs="?",
    help="Address of an IRC server, if not specified in the config.")
parser.add_argument("nick", nargs="?",
    help="Nickname to use when connecting, if not specified in the config.")
parser.add_argument("-c", "--config", default="bot.cfg",
    help="Path from which to load configuration data.")
parser.add_argument("-p", "--port", type=int, default=None,
    help="Port to use when connecting")
parser.add_argument("--username",
    help="Username to use. If not set, defaults to nickname.")
parser.add_argument("--realname",
    help="Real name to use. If not set, defaults to username.")
parser.add_argument("--password", default=None,
    help="IRC server password, if any (and if not using config file).")
parser.add_argument("--loglevel", default="INFO",
    help="Logging level for the root logger.",
    choices=["FATAL","ERROR","WARNING","INFO","DEBUG"])

# Note: this basic skeleton doesn't verify SSL certificates. See
# http://docs.python.org/2/library/ssl.html#ssl.wrap_socket and
# https://github.com/ayust/kitnirc/wiki/SSL-Connections for details.
parser.add_argument("--ssl", action="store_true",
    help="Use SSL to connect to the IRC server.")


def initialize_logging(args):
    """Configure the root logger with some sensible defaults."""
    log_handler = logging.StreamHandler()
    log_formatter = logging.Formatter(
        "%(levelname)s %(asctime)s %(name)s:%(lineno)04d - %(message)s")
    log_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)
    root_logger.setLevel(getattr(logging, args.loglevel))


def main():
    """Run the bot."""
    args = parser.parse_args()
    initialize_logging(args)

    # Allow expansion of paths even if the shell doesn't do it
    config_path = os.path.abspath(os.path.expanduser(args.config))

    client = kitnirc.client.Client()
    controller = kitnirc.modular.Controller(client, config_path)

    # Make sure the configuration file is loaded so we can check for
    # connection information.
    controller.load_config()

    def config_or_none(section, value, integer=False, boolean=False):
        """Helper function to get values that might not be set."""
        if controller.config.has_option(section, value):
            if integer:
                return controller.config.getint(section, value)
            elif boolean:
                return controller.config.getboolean(section, value)
            return controller.config.get(section, value)
        return None

    # If host isn't specified on the command line, try from config file
    host = args.host or config_or_none("server", "host")
    if not host:
        argparse.ArgumentParser.error(
            "IRC host must be specified if not in config file.")

    # If nick isn't specified on the command line, try from config file
    nick = args.nick or config_or_none("server", "nick")
    if not nick:
        argparse.ArgumentParser.error(
            "Nick must be specified if not in config file.")

    # KitnIRC's default client will use port 6667 if nothing else is specified,
    # but since we want to potentially specify something else, we add that
    # fallback here ourselves.
    port = args.port or config_or_none("server", "port", integer=True) or 6667
    ssl = args.ssl or config_or_none("server", "ssl", boolean=True)
    password = args.password or config_or_none("server", "password")
    username = args.username or config_or_none("server", "username") or nick
    realname = args.realname or config_or_none("server", "realname") or username

    controller.start()
    client.connect(
        nick,
        host=host,
        port=port,
        username=username,
        realname=realname,
        password=password,
        ssl=ssl,
    )
    try:
        client.run()
    except KeyboardInterrupt:
        client.disconnect()


if __name__ == "__main__":
    main()

# vim: set ts=4 sts=4 sw=4 et:

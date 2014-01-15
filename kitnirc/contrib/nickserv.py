#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from kitnirc.modular import Module

_log = logging.getLogger(__name__)

class NickServModule(Module):
    """A KitnIRC module which automatically authenticates
    nicks via NickServ.
    
    The module will attempt to authenticate with the nickserv
    using the password found in the nickserv section.
    [nickserv]
    ;password = <password>
    """
    
    @Module.handle("WELCOME")
    def register_nick(self, client, hostmask):

        _log.info("Beginning automatic nick configuration...")
        
        config = self.controller.config

        if config.has_option("nickserv", "password"):
            password = config.get("nickserv", "password")
            client.msg("NickServ", "IDENTIFY {}".format(password))
        else:
            _log.info("No password found for nickserv.")

        _log.info("Automatic nick configuration complete.")

module = NickServModule

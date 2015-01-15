import ConfigParser
import importlib
import inspect
import logging

_log = logging.getLogger(__name__)


# A copy of this is made for each instance of Controller when it is initialized
DEFAULT_SUBSTITUTIONS = {}


class Module(object):
    """A single module that can be loaded into a Controller."""

    def __init__(self, controller):
        self.controller = controller
        self.event_handlers = {}
        # Collect the results of the @Module.handle('EVENT') decorator
        for k,v in inspect.getmembers(self, inspect.ismethod):
            if hasattr(v, "_handle_events"):
                for event in v._handle_events:
                    self.add_handler(event, v)

    def add_handler(self, event, handler):
        """Adds a handler function for an event.

        Note: Only one handler function is allowed per event on the module
        level (different modules can provide handlers for the same event).
        This is because ordering of handler functions is not guaranteed to
        be preserved at the module level.

        Also note that it's probably easier and more succint to use the
        decorator form of this e.g. @Module.handle('EVENT')
        """
        if event in self.event_handlers:
            raise ValueError("Cannot register handler for '%s' twice." % event)
        self.event_handlers[event] = handler

    @staticmethod
    def handle(event):
        """Decorator for indicating that a given method handles an event.

        Note: while multiple instances of this decorator may be applied to a
        single method, it is not recommended.
        """
        def dec(func):
            if not hasattr(func, '_handle_events'):
                func._handle_events = set()
            func._handle_events.add(event)
            return func

        return dec

    def start(self, reloading=False):
        """Called when the module is loaded.

        If the load is due to a reload of the module, then the 'reloading'
        argument will be set to True. By default, this method calls the
        controller's listen() for each event in the self.event_handlers dict.
        """
        for event in self.event_handlers:
            self.controller.listen(event)

    def stop(self, reloading=False):
        """Called when the module is loaded.

        If the load is due to a reload of the module, then the 'reloading'
        argument will be set to True.
        """
        pass

    def handle_event(self, event, client, args):
        """Dispatch an event to its handler.

        Note: the handler does not receive the event which triggered its call.
        If you want to handle more than one event, it's recommended to put the
        shared handling in a separate function, and create wrapper handlers
        that call the shared function.
        """
        handler = self.event_handlers.get(event)
        if handler:
            return handler(client, *args)

    def trigger_event(self, event, client, args, force_dispatch=False):
        """Trigger a new event that will be dispatched to all modules."""
        self.controller.process_event(event, client, args, force_dispatch=force_dispatch)


class Controller(object):
    """A central module controller for a modular IRC bot.

    Provides facilities for loading, unloading, and reloading modules based
    on a configuration file, and for loading and saving that configuration.
    """

    def __init__(self, client, config_path=None):
        # Our kitnirc.client.Client instance
        self.client = client

        # Our loaded configuration object, if any
        self.config = None
        self.config_path = config_path

        # Our loaded modules, and an ordering amongst them
        self.loaded_modules = {}
        self.module_ordering = []

        # What events we have registered to receive
        self.registered = set()

        self.DEFAULT_SUBSTITUTIONS = dict(DEFAULT_SUBSTITUTIONS)

        # Whether incoming events should be dispatched or not
        self.running = False

        # Keep track of any modules we're actively loading, to prevent
        # reload loops. Also make sure that a given event doesn't
        # propagate past a module load.
        self.currently_loading = set()
        self.loaded_on_this_event = None

    def listen(self, event):
        """Request that the Controller listen for and dispatch an event.

        Note: Even if the module that requested the listening is later
        unloaded, the Controller will continue to dispatch the event, there
        just might not be anything that cares about it. That's okay.
        """
        if event in self.registered:
            # Already listening to this event
            return
        def handler(client, *args):
            return self.process_event(event, client, args)
        self.client.add_handler(event, handler)
        self.registered.add(event)
        _log.debug("Controller is now listening for '%s' events", event)

    def start(self):
        """Begin listening for events from the Client and acting upon them.

        Note: If configuration has not already been loaded, it will be loaded
        immediately before starting to listen for events. Calling this method
        without having specified and/or loaded a configuration will result in
        completely default values being used.

        After all modules for this controller are loaded, the STARTUP event
        will be dispatched.
        """
        if not self.config and self.config_path is not None:
            self.load_config()
        self.running = True
        self.process_event("STARTUP", self.client, ())

    def process_event(self, event, client, args, force_dispatch=False):
        """Process an incoming event.

        Offers it to each module according to self.module_ordering,
        continuing to the next unless the module inhibits propagation.

        Returns True if a module inhibited propagation, otherwise False.
        """
        if not self.running:
            _log.debug("Ignoring '%s' event - controller not running.", event)
            return

        # We keep a copy of the state of loaded modules before this event,
        # and restore it when we're done. This lets us handle events that
        # result in other events being dispatched in a graceful manner.
        old_loaded = self.loaded_on_this_event
        self.loaded_on_this_event = set(old_loaded or []) if not force_dispatch else set()

        try:
            _log.debug("Controller is dispatching '%s' event", event)
            for module_name in self.module_ordering:
                if module_name in self.loaded_on_this_event and not force_dispatch:
                    _log.debug("Not dispatching %s to '%s' because it was just "
                               "loaded (%r).", event, module_name,
                               self.loaded_on_this_event)
                    continue
                module = self.loaded_modules[module_name]
                if module.handle_event(event, client, args):
                    return True
        finally:
            self.loaded_on_this_event = old_loaded

    def initialize_config(self, config):
        """Writes default sections into the config."""
        # For storing what modules to load. Initially empty.
        # Should contain module_name=### pairs, where the numbers specify
        # relative priority - lower numbers are loaded first and get messages
        # first (this giving them higher priority).
        config.add_section("modules")

    def load_config(self, config_path=None):
        """Load configuration from the specified path, or self.config_path"""
        if config_path is None:
            config_path = self.config_path
        else:
            self.config_path = config_path

        config = ConfigParser.SafeConfigParser(self.DEFAULT_SUBSTITUTIONS,
                                               allow_no_value=True)
        # Avoid the configparser automatically lowercasing keys
        config.optionxform = str
        self.initialize_config(config)
        try:
            with open(config_path) as f:
                config.readfp(f)
        except (IOError, ConfigParser.Error):
            _log.exception("Ignoring config from %s due to error.", config_path)
            return False

        self.config = config
        self.reload_modules()
        return True

    def save_config(self, config_path=None):
        """Save configuration to the specified path, or self.config_path"""
        if config_path is None:
            config_path = self.config_path
        else:
            self.config_path = config_path

        with open(config_path, 'w') as f:
            self.config.write(f)

    def reload_modules(self):
        """(Re)load all of the configured modules.

        1. Calls stop(reloading=True) on each loaded module
        2. Clears .loaded_modules and .module_ordering
        3. Loads each module specified in the config
        4. Calls start() on each loaded module, with reloading set depending
           on whether the module was previously loaded or not
        5. Dispatches the STARTUP event, since all modules have been rebooted

        Returns True if all modules reloaded successfully, otherwise False.
        """
        old_modules = set(self.loaded_modules)
        for module in self.loaded_modules.itervalues():
            module.stop(reloading=True)

        self.loaded_modules = {}
        self.module_ordering = []

        try:
            modules_to_load = sorted(self.config.items("modules"),
                                     key=lambda x:int(x[1]))
        except (TypeError,ValueError):
            _log.exception("Unable to load modules due to invalid priority.")
            return False

        modules_success = []
        modules_failure = []

        for module_name,_ in modules_to_load:
            if self.load_module(module_name):
                modules_success.append(module_name)
            else:
                modules_failure.append(module_name)

        if modules_success:
            _log.info("Loaded the following modules: %s", modules_success)
        if modules_failure:
            _log.error("These modules failed to load: %s", modules_failure)

        for module_name in self.module_ordering:
            module = self.loaded_modules[module_name]
            module.start(reloading=(module_name in old_modules))

        self.process_event("STARTUP", self.client, (), force_dispatch=True)

        return not modules_failure

    def reload_module(self, module_name):
        """Reloads the specified module without changing its ordering.

        1. Calls stop(reloading=True) on the module
        2. Reloads the Module object into .loaded_modules
        3. Calls start(reloading=True) on the new object
        
        If called with a module name that is not currently loaded, it will load it.

        Returns True if the module was successfully reloaded, otherwise False.
        """
        module = self.loaded_modules.get(module_name)
        if module:
            module.stop(reloading=True)
        else:
            _log.info("Reload loading new module module '%s'",
                         module_name)
        success = self.load_module(module_name)
        if success:
            _log.info("Successfully (re)loaded module '%s'.", module_name)
        elif module:
            _log.error("Unable to reload module '%s', reusing existing.",
                       module_name)
        else:
            _log.error("Failed to load module '%s'.", module_name)
            return False
        self.loaded_modules[module_name].start(reloading=True)
        return success

    def load_module(self, module_name):
        """Attempts to load the specified module.

        If successful, .loaded_modules[module_name] will be populated, and
        module_name will be added to the end of .module_ordering as well if
        it is not already present. Note that this function does NOT call
        start()/stop() on the module - in general, you don't want to call
        this directly but instead use reload_module().

        Returns True if the module was successfully loaded, otherwise False.
        """
        if module_name in self.currently_loading:
            _log.warning("Ignoring request to load module '%s' because it "
                         "is already currently being loaded.", module_name)
            return False

        try: # ensure that currently_loading gets reset no matter what
            self.currently_loading.add(module_name)
            if self.loaded_on_this_event is not None:
                self.loaded_on_this_event.add(module_name)

            # Force the module to actually be reloaded
            try:
                _temp = reload(importlib.import_module(module_name))
            except ImportError:
                _log.error("Unable to load module '%s' - module not found.",
                           module_name)
                return False
            except SyntaxError:
                _log.exception("Unable to load module '%s' - syntax error(s).",
                           module_name)
                return False

            if not hasattr(_temp, "module"):
                _log.error("Unable to load module '%s' - no 'module' member.",
                           module_name)
                return False

            module = _temp.module
            if not issubclass(module, Module):
                _log.error("Unable to load module '%s' - it's 'module' member "
                           "is not a kitnirc.modular.Module.", module_name)
                return False

            self.loaded_modules[module_name] = module(self)
            if module_name not in self.module_ordering:
                self.module_ordering.append(module_name)
            return True

        finally:
            self.currently_loading.discard(module_name)

    def unload_module(self, module_name):
        """Unload the specified module, if it is loaded."""
        module = self.loaded_modules.get(module_name)
        if not module:
            _log.warning("Ignoring request to unload non-existant module '%s'",
                         module_name)
            return False

        module.stop(reloading=False)
        del self.loaded_modules[module_name]
        self.module_ordering.remove(module_name)
        return True

# vim: set ts=4 sts=4 sw=4 et:

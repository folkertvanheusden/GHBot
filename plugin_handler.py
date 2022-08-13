#! /usr/bin/python3

import importlib
import os
import sys

class plugins_class:
    def __init__(self, directory, name_prefix):
        self.directory = directory

        self.plugins   = dict()

        for filename in os.listdir(directory):
            if filename[0:len(name_prefix)] == name_prefix:
                name_only = filename.rstrip('.py')
                full_name = f'{directory}.{name_only}'
                self.plugins[name_only] = importlib.import_module(full_name)

    # returns True if any plugin processed the command
    def process(self, nick, command_text):
        for name in self.plugins:
            if self.plugins[name].process(nick, command_text):
                return True

        return False

    def list_plugins(self):
        return [name for name in self.plugins]

    def get_commandos(self, name):
        return self.plugins[name].get_commandos()

    def reload_module(self, name):
        ok = False

        full_name = f'{self.directory}.{name}'

        for k, v in list(sys.modules.items()):
            if full_name in k:
                importlib.reload(v)

                ok = True

        return ok

if __name__ == "__main__":
    plugin_subdir = 'plugins'  # relative path!!
    plugins = plugins_class(plugin_subdir, 'ghb_')

    print(plugins.list_plugins())

    plugins.process('test', '!door_open')

    print(plugins.get_commandos('ghb_door'))

    print(plugins.reload_module('ghb_door'))

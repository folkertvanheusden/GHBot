#! /usr/bin/python3

import importlib
import os
import sys

class plugins_class:
    def __init__(self, ghbot_instance, directory, name_prefix):
        print(self, ghbot_instance)
        self.ghbot       = ghbot_instance
        self.directory   = directory
        self.name_prefix = name_prefix

        self.plugins     = dict()

        self.load_modules()

    def load_modules(self):
        which = []

        for filename in os.listdir(self.directory):
            name_only = filename.rstrip('.py')

            if filename[0:len(self.name_prefix)] == self.name_prefix and not name_only in self.plugins:
                full_name = f'{self.directory}.{name_only}'
                self.plugins[name_only] = importlib.import_module(full_name)

                which.append(name_only)

        return which

    # returns True if any plugin processed the command
    def process(self, nick, parameters):
        print('process')

        for name in self.plugins:
            print(f'trying {name}')

            if self.plugins[name].process(self.ghbot, nick, parameters):
                print(f'plugin said ok')
                return True

        print('no matches')

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

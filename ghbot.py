#! /usr/bin/python3

import configparser
from dbi import dbi
from enum import Enum
from http_server import http_server
from ircbot import ircbot, irc_keepalive
import math
from mqtt_handler import mqtt_handler
from plugin_handler import plugins_class
import random
import select
import socket
import sys
import threading
import time
import traceback


class ghbot(ircbot):
    class internal_command_rc(Enum):
        HANDLED      = 0x00
        ERROR        = 0x10
        NOT_INTERNAL = 0xff

    def __init__(self, host, port, nick, channels, m, db, cmd_prefix, local_plugin_subdir):
        super().__init__(host, port, nick, channels)

        self.cmd_prefix    = cmd_prefix

        self.db            = db

        self.mqtt          = m

        self.plugins       = dict()
        self.plugins_lock  = threading.Lock()
        self.plugins_gone  = dict()

        self.local_plugins = plugins_class(self, local_plugin_subdir, 'ghb_')

        now                = time.time()

        #                          v make these into dictionaries v  TODO
        self.plugins['addacl']   = ['Add an ACL, format: addacl user|group <user|group> group|cmd <group-name|cmd-name>', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['delacl']   = ['Remove an ACL, format: delacl <user> group|cmd <group-name|cmd-name>', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['listacls'] = ['List all ACLs for a user or group', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['deluser']  = ['Forget a person; removes all ACLs for that nick', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['clone']    = ['Clone ACLs from one user to another', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['meet']     = ['Use this when a user (nick) has a new hostname: meet <nick>', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['commands'] = ['Show list of known commands', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['help']     = ['Help for commands, parameter is the command to get help for', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['more']     = ['Continue outputting a too long line of text', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['define']   = ['Define a command that will be replied to with a definable text, format: !define <command> <text... with %m (/me), %q (parameters) and %u (nick of invoker) escapes>', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['deldefine']= ['Delete a define (by number)', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['alias']    = ['Add a different name for a command, format: !alias <newname> <oldname>', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['searchdefine'] = ['Search for defines that match a partial text', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['searchalias'] = ['Search for aliases that match a partial text', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['viewalias'] = ['Show what an alias is doing', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['listgroups']= ['Shows a list of available groups', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['showgroup']= ['Shows a list of commands or members in a group (showgroup commands|members <groupname>)', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['apro']     = ['Show commands that match a partial text', None, now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['reloadlp'] = ['Reload a "local" plugin', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['listlp']   = ['List "local" plugins', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['showlp']   = ['Show commands of a "local" plugin', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']
        self.plugins['loadlp']   = ['Load "local" plugins that are not loaded yet', 'sysops', now, 'Flok', 'harkbot.vm.nurd.space']

        self.hardcoded_plugins = set()
        for p in self.plugins:
            self.hardcoded_plugins.add(p)

        for local_plugin in self.local_plugins.list_plugins():  # iterate over each plugin .py-file
            all_commands = self.local_plugins.get_commandos(local_plugin)

            for command, parameters in all_commands:  # iterate over each command that a plugin can have
                # they're hardcoded; don't allow to override
                self.hardcoded_plugins.add(command)
                # register in the plugin-list
                self.plugins[command] = parameters

        self.topic_privmsg = []
        self.topic_notice  = []
        self.topic_topic   = []

        self.topic_to_nick = f'to/irc-person/'

        for channel in self.channels:
            self.topic_privmsg.append(f'to/irc/{channel[1:]}/privmsg')  # Send reply in channel via PRIVMSG
            self.topic_notice.append(f'to/irc/{channel[1:]}/notice')   # Send reply in channel via NOTICE
            self.topic_topic.append(f'to/irc/{channel[1:]}/topic')    # Sets TOPIC for channel

        self.topic_register = f'to/bot/register'  # topic where plugins announce themselves

        self.topic_request = f'to/bot/request'  # topic where plugins request bot-actions

        self.mqtt.subscribe(self.topic_request, self._recv_msg_cb)

        for topic in self.topic_privmsg:
            self.mqtt.subscribe(topic, self._recv_msg_cb)

        for topic in self.topic_notice:
            self.mqtt.subscribe(topic, self._recv_msg_cb)

        for topic in self.topic_topic:
            self.mqtt.subscribe(topic, self._recv_msg_cb)

        self.mqtt.subscribe('to/irc/#', self._recv_msg_cb)  # required for pm-commands :-/
        self.pm_topic = 'to/irc/\\'  # to match on

        self.mqtt.subscribe(self.topic_to_nick + '#', self._recv_msg_cb)

        self.mqtt.subscribe(self.topic_register, self._recv_msg_cb)

        self.host        = host
        self.port        = port
        self.nick        = nick
        self.channel     = channel

        self.fd          = None

        self.state       = self.session_state.DISCONNECTED
        self.state_since = time.time()

        self.users       = dict()

        self.name = 'GHBot IRC'
        self.start()

        self.plugin_cleaner = threading.Thread(target=self._plugin_cleaner)
        self.plugin_cleaner.start()

        # ask plugins to register themselves so that we know which
        # commands are available (and what they're for etc.)
        self._plugin_command('register')

        self._plugin_parameter('prefix', self.cmd_prefix, True)

    # checks how old the the latest registration of a plugin is.
    # too old? (10 seconds) then forget the plugin-command.
    def _plugin_cleaner(self):
        while True:
            try:
                time.sleep(4.9)

                to_delete = []

                now       = time.time()

                self.plugins_lock.acquire()

                for plugin in self.plugins:
                    if now - self.plugins[plugin][2] >= 10. and plugin not in self.hardcoded_plugins:  # 5 seconds timeout
                        to_delete.append(plugin)

                for plugin in to_delete:
                    del self.plugins[plugin]

                    self.plugins_gone[plugin] = now

                self.plugins_lock.release()

            except Exception as e:
                print(f'_plugin_cleaner: failed to clean: {e}')

    def _plugin_command(self, cmd):
        self.mqtt.publish('from/bot/command', cmd, persistent=False)

    def _plugin_parameter(self, key, value, persistent):
        self.mqtt.publish(f'from/bot/parameter/{key}', value, persistent=persistent)

    def _register_plugin(self, msg):
        self.plugins_lock.acquire()

        try:
            elements = msg.split('|')

            cmd       = None
            descr     = ''
            acl_group = None
            athr      = ''
            location  = ''

            for element in elements:
                k, v = element.split('=')

                if k == 'cmd':
                    cmd = v
                
                elif k == 'descr':
                    descr = v

                elif k == 'agrp':
                    acl_group = v

                elif k == 'athr':
                    athr = v

                elif k == 'loc':
                    location = v

            if cmd != None:
                if not cmd in self.hardcoded_plugins:
                    if not cmd in self.plugins:
                        print(f'_register_plugin: first announcement of {cmd}')

                    self.plugins[cmd] = [descr, acl_group, time.time(), athr, location]

                    if cmd in self.plugins_gone:
                        del self.plugins_gone[cmd]

                else:
                    print(f'_register_plugin: cannot override "hardcoded" plugin ({cmd})')

            else:
                print(f'_register_plugin: cmd missing in plugin registration')

        except Exception as e:
            print(f'_register_plugin: problem while processing plugin registration "{msg}": {e}')

        self.plugins_lock.release()

    def _send_topics_to_plugins(self):
        for channel in self.topics:
            self.mqtt.publish(f'from/irc/{channel}/topic', self.topics[channel])

    def _recv_msg_cb(self, topic, msg):
        try:
            # print(f'irc::_recv_msg_cb: received "{msg}" for topic {topic}')

            topic = topic[len(self.mqtt.get_topix_prefix()):]

            parts = topic.split('/')

            if msg.find('\n') != -1 or msg.find('\r') != -1:
                print(f'irc::_recv_msg_cb: invalid content to send for {topic}')

                return

            if topic in self.topic_privmsg:
                self.send_ok('#' + parts[2], self.escapes(msg))

            elif topic in self.topic_notice:
                self.send(f'NOTICE #{parts[2]} :{msg}')

            elif topic in self.topic_topic:
                self.send(f'TOPIC #{parts[2]} :{msg}')

            elif topic in self.topic_request:
                print(f'plugin requested {msg}')
                if msg == 'topics':
                    self._send_topics_to_plugins()

            elif topic in self.topic_register:
                self._register_plugin(msg)

            elif parts[0] + '/' + parts[1] in self.topic_to_nick:
                nick = parts[2]

                if nick[0] == '\\':
                    nick = nick[1:]

                self.send(f'PRIVMSG {nick} :{msg}')

            elif self.pm_topic in topic:
                nick = parts[2][1:]  # remove '\'

                self.send(f'PRIVMSG {nick} :{msg}')

            else:
                print(f'irc::_recv_msg_cb: invalid topic {topic}')

                return

        except Exception as e:
            print(f'irc::_recv_msg_cb: exception {e} while processing {topic}|{msg} (at line number: {e.__traceback__.tb_lineno})')

    def check_acls(self, who, command):
        self.plugins_lock.acquire()

        # "no group" is for everyone
        if command in self.plugins and self.plugins[command][1] == None:
            self.plugins_lock.release()

            return (True, None)

        plugin_group = self.plugins[command][1]

        self.plugins_lock.release()

        self.db.probe()  # to prevent those pesky "sever has gone away" problems

        cursor = self.db.db.cursor()

        # check per user ACLs (can override group as defined in plugin)
        cursor.execute('SELECT COUNT(*) FROM acls WHERE command=%s AND who=%s', (command.lower(), who.lower()))

        row = cursor.fetchone()

        if row[0] >= 1:
            return (True, plugin_group)

        # check per group ACLs (can override group as defined in plugin)
        cursor.execute('SELECT COUNT(*) FROM acls, acl_groups WHERE acl_groups.who=%s AND acl_groups.group_name=acls.who AND command=%s', (who.lower(), command.lower()))

        row = cursor.fetchone()

        if row[0] >= 1:
            return (True, plugin_group)

        # check if user is in group as specified by plugin
        cursor.execute('SELECT COUNT(*) FROM acl_groups WHERE group_name=%s AND who=%s', (plugin_group, who))

        row = cursor.fetchone()

        if row[0] >= 1:
            return (True, plugin_group)

        return (False, plugin_group)

    def list_acls(self, who):
        self.db.probe()

        cursor = self.db.db.cursor()

        cursor.execute('SELECT DISTINCT item FROM (SELECT command AS item FROM acls WHERE who=%s UNION SELECT group_name AS item FROM acl_groups WHERE who=%s) AS in_ ORDER BY item', (who.lower(), who.lower()))

        out = []

        for row in cursor:
            out.append(row[0])

        return out

    def add_acl(self, who, command):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('INSERT INTO acls(command, who) VALUES(%s, %s)', (command.lower(), who.lower()))

            self.db.db.commit()

            return (True, 'Ok')

        except Exception as e:
            return (False, f'irc::add_acl: failed to insert acl ({e})')

    def del_acl(self, who, command):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM acls WHERE command=%s AND who=%s LIMIT 1', (command.lower(), who.lower()))

            self.db.db.commit()

            if cursor.rowcount == 1:
                return (True, 'Ok')

            return (False, 'That command/nick combination was not known')

        except Exception as e:
            return (False, f'irc::del_acl: failed to delete acl ({e})')

    def forget_acls(self, who):
        match_ = who + '!%'

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM acls WHERE who LIKE %s', (match_,))
            any_del = cursor.rowcount == 1

            cursor.execute('DELETE FROM acl_groups WHERE who LIKE %s', (match_,))
            any_del |= cursor.rowcount == 1

            self.db.db.commit()

            if any_del:
                return (True, 'Ok')

            return (False, 'No acls found for that nick')

        except Exception as e:
            return (False, f'irc::forget_acls: failed to forget acls for {match_}: {e}')

    def clone_acls(self, from_, to_):
        cursor = self.db.db.cursor()

        try:
            cursor.execute('SELECT group_name FROM acl_groups WHERE who=%s', (from_,))

            for row in cursor.fetchall():
                cursor.execute('INSERT INTO acl_groups(group_name, who) VALUES(%s, %s)', (row, to_))

            self.db.db.commit()

            return (True, 'Ok')

        except Exception as e:
            return (False, f'failed to clone acls: {e}')

    # new_fullname is the new 'nick!user@host'
    def update_acls(self, who, new_fullname):
        self.db.probe()

        match_ = who + '!%'

        cursor = self.db.db.cursor()

        try:
            cursor.execute('UPDATE acls SET who=%s WHERE who LIKE %s', (new_fullname, match_))

            cursor.execute('UPDATE acl_groups SET who=%s WHERE who LIKE %s', (new_fullname, match_))

            self.db.db.commit()

            return (True, 'Ok')

        except Exception as e:
            return (False, f'irc::update_acls: failed to update acls ({e})')

    def group_add(self, who, group):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('INSERT INTO acl_groups(who, group_name) VALUES(%s, %s)', (who.lower(), group.lower()))

            self.db.db.commit()

            return (True, 'Ok')

        except Exception as e:
            return (False, f'irc::group_add: failed to insert group-member ({e})')

    def group_del(self, who, group):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM acl_groups WHERE who=%s AND group_name=%s LIMIT 1', (who.lower(), group.lower()))

            self.db.db.commit()

            if cursor.rowcount == 1:
                return (True, 'Ok')

            return (False, 'That user/group combination was not known')

        except Exception as e:
            return (False, f'irc::group-del: failed to delete group-member ({e}, {e.__traceback__.tb_lineno})')

    def check_user_known(self, user):
        if '!' in user:
            for cur_user in self.users:
                if self.users[cur_user] == user:
                    return True

            return False

        if not user in self.users:
            return False

        if self.users[user] == None or self.users[user] == '?':
            return False

        return True

    def is_group(self, group):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('SELECT COUNT(*) FROM acl_groups WHERE group_name=%s LIMIT 1', (group.lower(), ))

            row = cursor.fetchone()

            if row[0] >= 1:
                return True

        except Exception as e:
            send_notice(self.owner, f'irc::is_group: failed to query database for group {group} ({e})')

        return False

    # e.g. 'group', 'bla' where 'group' is the key and 'bla' the value
    def find_key_in_list(self, list_, item, search_start):
        try:
            idx = list_.index(item, search_start)

            # check if an argument is following it
            if idx == len(list_) - 1:
                idx = None

        except ValueError as ve:
            idx = None

        return idx

    def invoke_who_and_wait(self, user):
        self.send(f'WHO {user}')

        start = time.time()

        while self.check_user_known(user) == False:
            t_diff = time.time() - start

            if t_diff >= 5.0:
                break

            with self.cond_352:
                self.cond_352.wait(5.0 - t_diff)

    def list_plugins(self):
        self.plugins_lock.acquire()

        plugins = ', '.join(sorted(self.plugins))

        self.plugins_lock.release()

        return plugins

    def add_define(self, command, is_alias, arguments):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('INSERT INTO aliasses(command, is_command, replacement_text) VALUES(%s, %s, %s)', (command.lower(), 1 if is_alias else 0, arguments))

            self.db.db.commit()

            return (True, cursor.lastrowid, 'Ok')

        except Exception as e:
            return (False, -1, f'irc::add_define: failed to insert alias ({e})')

    def del_define(self, nr):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM aliasses WHERE nr=%s', (nr,))

            self.db.db.commit()

            if cursor.rowcount == 1:
                return (True, 'Ok')

            return (False, f'irc::del_define: unexpected affected rows count {cursor.rowcount}')

        except Exception as e:
            return (False, f'irc::del_define: failed to delete alias {nr} ({e})')

    def search_define(self, what):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('SELECT command, nr, replacement_text FROM aliasses WHERE nr=%s OR command like %s ORDER BY nr DESC', (what, f'%%{what.lower()}%%',))

            results = []

            for row in cursor:
                results.append(row)

            cursor.close()

            if len(results) > 0:
                return (results, True, 'Ok')

        except Exception as e:
            return (None, False, f'irc::del_define: failed to delete alias {nr} ({e})')

        return (None, True, 'None')

    def escapes(self, text):
        if '%R' in text:
            text = text.replace('%R', f'{random.randint(0, 100)}')

        if '%m' in text:
            text = text.strip('%m')

            text = '\001ACTION ' + text.strip() + '\001'

        return text

    def check_aliasses(self, text, username):
        parts   = text.split(' ')
        command = parts[0]

        cursor  = self.db.db.cursor()

        cursor.execute('SELECT is_command, replacement_text FROM aliasses WHERE command=%s ORDER BY RAND() LIMIT 1', (command.lower(), ))

        row = cursor.fetchone()

        if row == None:
            return (False, None, False)

        is_command = row[0]
        repl_text  = row[1]

        space      = text.find(' ')

        if space == -1:
            query_text = username

            if '!' in query_text:
                query_text = query_text[0:query_text.find('!')]

        else:
            query_text = text[space + 1:]

        if is_command:  # initially only replaces command
            text = repl_text + ' ' + query_text

        else:
            text = repl_text

        text = self.escapes(text)

        if username != None:
            exclamation_mark = username.find('!')

            if exclamation_mark != -1:
                username = username[0:exclamation_mark]

            text = text.replace('%u', username)

        text = text.replace('%q', query_text)

        notice = False

        if '%n' in text:
            text = text.replace('%n', '')

            notice = True

        return (is_command, text, notice)

    def invoke_internal_commands(self, prefix, command, splitted_args, channel):
        identifier  = None

        target_type = None

        check_user  = '(not given)'

        if channel == self.nick:
            channel = prefix

            if '!' in channel:
                channel = channel[0:channel.find('!')]

        if splitted_args != None and len(splitted_args) >= 2:
            if len(splitted_args) >= 3:  # addacl
                target_type = splitted_args[1]

                check_user  = splitted_args[2].lower()

            else:
                target_type = None

                check_user  = splitted_args[1].lower()

            if check_user in self.users:
                identifier = self.users[check_user]

            elif '!' in check_user:
                identifier = check_user

            elif self.is_group(check_user):
                identifier = check_user

#        print(f'identifier {identifier}, user known: {self.check_user_known(identifier)}, is group: {self.is_group(identifier)}')

        identifier_is_known = (self.check_user_known(identifier) or self.is_group(identifier)) if identifier != None else False

        if command == 'addacl':
            group_idx = self.find_key_in_list(splitted_args, 'group', 2)

            cmd_idx   = self.find_key_in_list(splitted_args, 'cmd',   2)

            if not identifier_is_known and target_type == 'user':
                self.invoke_who_and_wait(check_user)

                if check_user in self.users:
                    identifier = self.users[check_user]

            # print(identifier, check_user, splitted_args)

            if group_idx != None:
                group_name = splitted_args[group_idx + 1]

                rc = self.group_add(identifier, group_name)  # who, group
                if rc[0]:
                    self.send_ok(channel, f'User {identifier} added to group {group_name}')

                    return self.internal_command_rc.HANDLED

                else:
                    self.send_error(channel, rc[1])

                    return self.internal_command_rc.ERROR

            elif cmd_idx != None:
                cmd_name = splitted_args[cmd_idx + 1]

                self.plugins_lock.acquire()

                plugin_known = cmd_name in self.plugins

                self.plugins_lock.release()

                if plugin_known:
                    rc = self.add_acl(identifier, cmd_name)  # who, command
                    if rc[0]:  # who, command
                        self.send_ok(channel, f'ACL added for user or group {identifier} for command {cmd_name}')

                        return self.internal_command_rc.HANDLED

                    else:
                        self.send_error(channel, f'Failed to add ACL - did it exist already? ({rc[1]})')

                        return self.internal_command_rc.ERROR

                else:
                    self.send_error(channel, f'ACL added for user {identifier} for command {cmd_name} NOT added: command/plugin not known')

                    return self.internal_command_rc.HANDLED

            else:
                self.send_error(channel, f'Usage: addacl user|group <user|group> group|cmd <group-name|cmd-name>')

                return self.internal_command_rc.ERROR

        elif command == 'delacl':
            group_idx = self.find_key_in_list(splitted_args, 'group', 2)

            cmd_idx   = self.find_key_in_list(splitted_args, 'cmd',   2)

            if not identifier_is_known and target_type == 'user':
                self.invoke_who_and_wait(check_user)

                if check_user in self.users:
                    identifier = self.users[check_user]

            if group_idx != None:
                group_name = splitted_args[group_idx + 1]

                rc = self.group_del(identifier, group_name)  # who, group
                if rc[0]:  # who, group
                    self.send_ok(channel, f'User {identifier} removed from group {group_name}')

                    return self.internal_command_rc.HANDLED

                else:
                    self.send_error(channel, rc[1])

                    return self.internal_command_rc.ERROR

            elif cmd_idx != None:
                cmd_name = splitted_args[cmd_idx + 1]

                rc = self.del_acl(identifier, cmd_name)  # who, command
                if rc[0]:  # who, command
                    self.send_ok(channel, f'ACL removed for user {identifier} for command {cmd_name}')

                    return self.internal_command_rc.HANDLED

                else:
                    self.send_error(channel, f'Failed to delete ACL ({rc[1]})')

                    return self.internal_command_rc.ERROR

            else:
                self.send_error(channel, f'Usage: delacl user <user> group|cmd <group-name|cmd-name>')

                return self.internal_command_rc.ERROR

        elif command == 'listacls':
            if not identifier_is_known:
                self.invoke_who_and_wait(check_user)

                if check_user in self.users:
                    identifier = self.users[check_user]

            if identifier != None:
                acls = self.list_acls(identifier)

                str_acls = ', '.join(acls)

                self.send_ok(channel, f'ACLs for user {identifier}: "{str_acls}"')

            else:
                self.send_error(channel, 'Please provide a nick')

            return self.internal_command_rc.HANDLED

        elif command == 'meet':
            if splitted_args != None and len(splitted_args) == 2:
                user_to_update = splitted_args[1]

                self.invoke_who_and_wait(user_to_update)

                if user_to_update in self.users:
                    ok, error_text = self.update_acls(user_to_update, self.users[user_to_update])

                    if ok:
                        self.send_ok(channel, f'User {user_to_update} updated to {self.users[user_to_update]}')

                    else:
                        self.send_error(channel, error_text)

                else:
                    self.send_error(channel, f'User {user_to_update} is not known')

            else:
                self.send_error(channel, f'Meet parameter missing ({splitted_args} given)')

        elif command == 'commands':
            plugins = self.list_plugins()

            self.send_ok(channel, f'Known commands: {plugins}')

            return self.internal_command_rc.HANDLED

        elif command == 'define' or command == 'alias':
            if len(splitted_args) >= 3:
                self.plugins_lock.acquire()

                plugin_known = splitted_args[1] in self.plugins

                self.plugins_lock.release()

                if plugin_known:
                    self.send_error(channel, f'Cannot override internal/plugin commands')

                else:
                    is_alias      = command == 'alias'
                    also_known_as = ' '.join(splitted_args[2:])

                    if is_alias and also_known_as[0] == '!':
                        also_known_as = also_known_as[1:]

                    rc, nr, err = self.add_define(splitted_args[1], is_alias, also_known_as)

                    if rc == True:
                        self.send_ok(channel, f'{command} added (number: {nr})')

                    else:
                        self.send_error(channel, f'Failed to add {command}: {err}')

            else:
                self.send_error(channel, f'{command} missing arguments')

        elif command == 'searchdefine' or command == 'searchalias':
            if len(splitted_args) >= 2:
                found, ok, err = self.search_define(splitted_args[1])

                if found != None:
                    defines = None

                    for entry in found:
                        if defines == None:
                            defines = ''

                        else:
                            defines += ', '

                        defines += f'{entry[0]}: {entry[1]}'

                    self.send_ok(channel, defines)

                elif ok:
                    self.send_error(channel, 'None found')

                else:
                    self.send_error(channel, '{err}')

            else:
                self.send_error(channel, f'{command} missing arguments')

        elif command == 'viewalias':
            if len(splitted_args) >= 2:
                found, ok, err = self.search_define(splitted_args[1])

                if found != None:
                    rc = f'{splitted_args[1]}: {found[0][2]}'

                    self.send_ok(channel, rc)

                elif ok:
                    self.send_error(channel, 'None found')

                else:
                    self.send_error(channel, '{err}')

            else:
                self.send_error(channel, f'{command} missing arguments')

        elif command == 'deldefine':
            if len(splitted_args) == 2:
                try:
                    nr = int(splitted_args[1])

                    rc, err = self.del_define(nr)

                    if rc == True:
                        self.send_ok(channel, f'Define {nr} deleted')

                    else:
                        self.send_error(channel, f'Failed to delete {nr}: {err}')

                except ValueError as ve:
                    self.send_error(channel, f'Parameter {splitted_args[1]} is not a number')

            else:
                self.send_error(channel, f'{command} missing arguments')

        elif command == 'help':
            if len(splitted_args) == 2:
                cmd = splitted_args[1]

                self.plugins_lock.acquire()

                if cmd in self.plugins:
                    self.send_ok(channel, f'Command {cmd}: {self.plugins[cmd][0]} (group: {self.plugins[cmd][1]})')

                else:
                    self.send_error(channel, f'Command/plugin not known')

                self.plugins_lock.release()

            else:
                plugins = self.list_plugins()

                self.send_ok(channel, f'Known commands: {plugins}')

            return self.internal_command_rc.HANDLED

        elif command == 'more':
            self.send_more(channel)

            return self.internal_command_rc.HANDLED

        elif command == 'deluser':
            if len(splitted_args) == 2:
                user = splitted_args[1]

                if '%' in user:
                    self.send_error(channel, f'User {user} not known or some other error')
                    return self.internal_command_rc.ERROR

                rc = self.forget_acls(user)
                if rc[0]:
                    self.send_ok(channel, f'User {user} forgotten')

                else:
                    self.send_error(channel, f'User {user} not known or some other error ({rc[1]})')

            else:
                self.send_error(channel, f'User not specified')

            return self.internal_command_rc.HANDLED

        elif command == 'clone':
            if len(splitted_args) == 3:
                from_ = splitted_args[1]
                to_   = splitted_args[2]

                from_user = from_.split('!')[0] if '!' in from_ else from_
                to_user   = to_.split('!')[0]   if '!' in to_   else to_

                if not from_user in self.users or self.users[from_user] == '?':
                    self.invoke_who_and_wait(from_user)

                if not to_user in self.users or self.users[to_user] == '?':
                    self.invoke_who_and_wait(to_user)

                if from_user in self.users and to_user in self.users and self.users[from_user] != '?' and self.users[to_user] != '?':
                    error = self.clone_acls(self.users[from_user], self.users[to_user])

                    if error == None:
                        self.send_ok(channel, f'User {from_} cloned (to {to_})')

                    else:
                        self.send_error(channel, f'Cannot clone {from_} to {to_}: {error}')

                else:
                    self.send_error(channel, f'Either {from_} or {to_} is unknown')

            else:
                self.send_error(channel, f'User "from" and/or "to" not specified')

            return self.internal_command_rc.HANDLED

        elif command == 'listgroups':
            try:
                cursor = self.db.db.cursor()

                cursor.execute('SELECT DISTINCT who FROM acls')

                groups = set()

                # defined by sysop(s)
                for row in cursor.fetchall():
                    groups.add(row[0])

                cursor.close()

                # defined by plugins
                self.plugins_lock.acquire()

                for plugin in self.plugins:
                    group = self.plugins[plugin][1]

                    if group != None:
                        groups.add(group)

                self.plugins_lock.release()

                groups_str = ', '.join(groups) if len(groups) > 1 else '(none)'

                self.send_ok(channel, f'Defined groups: {groups_str}')

            except Exception as e:
                self.send_error(channel, f'listgroups: exception "{e}" at line number: {e.__traceback__.tb_lineno}')

            return self.internal_command_rc.HANDLED

        elif command == 'showgroup':
            if len(splitted_args) == 3:
                which = splitted_args[1]
                group = splitted_args[2]

                cursor = self.db.db.cursor()

                if which.lower() == 'commands':
                    cursor.execute('SELECT command FROM acls WHERE who=%s', (group,))

                    commands = set()

                    # defined by sysop(s)
                    for row in cursor.fetchall():
                        commands.add(row[0])

                    # defined by plugins
                    self.plugins_lock.acquire()

                    for plugin in self.plugins:
                        if self.plugins[plugin][1] == group:
                            commands.add(plugin)

                    self.plugins_lock.release()

                    cursor.close()

                    commands_str = ', '.join(commands)

                    self.send_ok(channel, f'Commands in group {group}: {commands_str}')

                elif which.lower() == 'members':
                    cursor.execute('SELECT who FROM acl_groups WHERE group_name=%s', (group,))

                    members = set()

                    for row in cursor.fetchall():
                        member = row[0]

                        if '!' in member:
                            member = member[0:member.find('!')]

                        members.add(member)

                    cursor.close()

                    members_str = ', '.join(members)

                    self.send_ok(channel, f'Members in group {group}: {members_str}')

            else:
                self.send_error(channel, 'Command is: showgroup members|commands <groupname>')

            return self.internal_command_rc.HANDLED

        elif command == 'apro':
            matching = set()

            which = splitted_args[1].lower()

            for plugin in self.plugins:
                if which in plugin:
                    matching.add(plugin)

            for plugin in self.hardcoded_plugins:
                if which in plugin:
                    matching.add(plugin)

            rc, err = self.find_alias_define_by_substring(which)

            if err != None:
                self.send_error(channel, f'Apro: {err}')

            else:
                for alias_define in rc:
                    matching.add(f'{alias_define[0]} ({alias_define[1]}, {alias_define[2]})')

                if len(matching) == 0:
                    self.send_ok(channel, f'Nothing matches with "{which}"')

                else:
                    self.send_ok(channel, f'Apro "{which}": ' + ', '.join(matching))

            return self.internal_command_rc.HANDLED

        elif command == 'reloadlp':
            matching = set()

            which = splitted_args[1].lower()

            if which in self.local_plugins.list_plugins():
                if self.local_plugins.reload_module(which):
                    self.send_ok(channel, f'Local plugins {which} reloaded')

                    return self.internal_command_rc.HANDLED

            self.send_ok(channel, f'Local plugins {which} NOT reloaded')

            return self.internal_command_rc.HANDLED

        elif command == 'loadlp':
            which = self.local_plugins.load_modules()

            self.send_ok(channel, f'Done (loaded: {", ".join(which)})')

            return self.internal_command_rc.HANDLED

        elif command == 'listlp':
            self.send_ok(channel, f'Local plugins: {", ".join(self.local_plugins.list_plugins())}')

            return self.internal_command_rc.HANDLED

        elif command == 'showlp':
            which = splitted_args[1].lower()

            all_commands = self.local_plugins.get_commandos(which)

            commands = [command for command, parameters in all_commands]

            self.send_ok(channel, f'Local plugins: {", ".join(commands)}')

            return self.internal_command_rc.HANDLED

        elif self.local_plugins.process(identifier, (prefix, command, splitted_args, channel)):
            return self.internal_command_rc.HANDLED

        return self.internal_command_rc.NOT_INTERNAL

    def find_alias_define_by_substring(self, which):
        try:
            self.db.probe()

            cursor = self.db.db.cursor()
            cursor.execute('SELECT command, is_command, nr FROM aliasses WHERE command like %s', (f'%%{which}%%',))

            rows = []

            for row in cursor:
                rows.append((row[0], 'alias' if row[1] == 1 else 'define', row[2]))

            return (rows, None)

        except Exception as e:
            return (rows, f'irc::add_define: failed to insert alias ({e})')

    def irc_command_insertion_point(self, prefix, command, arguments):
        if command in [ 'JOIN', 'PART', 'KICK', 'NICK', 'QUIT' ]:
            self.mqtt.publish(f'from/irc/{arguments[0][1:]}/{prefix}/{command}', ' '.join(arguments))

        return True

if len(sys.argv) != 2:
    print('Filename of configuration file required')

    sys.exit(1)

config = configparser.ConfigParser()
config.read(sys.argv[1])

# host, user, password, database
db = dbi(config['db']['host'], config['db']['user'], config['db']['password'], config['db']['database'])

# broker_ip, topic_prefix
m = mqtt_handler(config['mqtt']['host'], config['mqtt']['prefix'])

# host, port, nick, channel, m, db, command_prefix
g = ghbot(config['irc']['host'], int(config['irc']['port']), config['irc']['nick'], config['irc']['channels'].split(','), m, db, config['irc']['prefix'], 'plugins')

ka = irc_keepalive(g)

h = http_server(8000, g)

print('Go!')

while True:
    time.sleep(3600.)

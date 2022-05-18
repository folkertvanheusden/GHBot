#! /usr/bin/python3

from dbi import dbi
from enum import Enum
from ircbot import ircbot, irc_keepalive
import math
from mqtt_handler import mqtt_handler
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

    def __init__(self, host, port, nick, channel, m, db, cmd_prefix):
        super().__init__(host, port, nick, channel)

        self.cmd_prefix  = cmd_prefix

        self.db          = db

        self.mqtt        = m

        self.plugins     = dict()
        self.plugins_lock= threading.Lock()

        now              = time.time()

        self.plugins['addacl']   = ['Add an ACL, format: addacl user|group <user|group> group|cmd <group-name|cmd-name>', 'sysops', now]
        self.plugins['delacl']   = ['Remove an ACL, format: delacl <user> group|cmd <group-name|cmd-name>', 'sysops', now]
        self.plugins['listacls'] = ['List all ACLs for a user or group', 'sysops', now]
        self.plugins['forget']   = ['Forget a person; removes all ACLs for that nick', 'sysops', now]
        self.plugins['clone']    = ['Clone ACLs from one user to another', 'sysops', now]
        self.plugins['meet']     = ['Use this when a user (nick) has a new hostname', 'sysops', now]
        self.plugins['commands'] = ['Show list of known commands', None, now]
        self.plugins['help']     = ['Help for commands, parameter is the command to get help for', None, now]
        self.plugins['more']     = ['Continue outputting a too long line of text', None, now]
        self.plugins['define']   = ['Define a replacement for text, see ~alias', None, now]
        self.plugins['deldefine']= ['Delete a define (by number)', None, now]
        self.plugins['alias']    = ['Add a different name for a command', None, now]

        self.hardcoded_plugins = set()
        for p in self.plugins:
            self.hardcoded_plugins.add(p)

        self.topic_privmsg  = f'to/irc/{channel[1:]}/privmsg'  # Send reply in channel via PRIVMSG
        self.topic_notice   = f'to/irc/{channel[1:]}/notice'   # Send reply in channel via NOTICE
        self.topic_topic    = f'to/irc/{channel[1:]}/topic'    # Sets TOPIC for channel

        self.topic_register = f'to/bot/register'  # topic where plugins announce themselves

        self.mqtt.subscribe(self.topic_privmsg,  self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_notice,   self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_topic,    self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_register, self._recv_msg_cb)

        self.host        = host
        self.port        = port
        self.nick        = nick
        self.channel     = channel

        self.fd          = None

        self.state       = self.session_state.DISCONNECTED
        self.state_since = time.time()

        self.users       = dict()

        self.more        = ''

        self.name = 'GHBot IRC'
        self.start()

        self.plugin_cleaner = threading.Thread(target=self._plugin_cleaner)
        self.plugin_cleaner.start()

        # ask plugins to register themselves so that we know which
        # commands are available (and what they're for etc.)
        self._plugin_command('register')

    # checks how old the the latest registration of a plugin is.
    # too old? then forget the plugin-command.
    def _plugin_cleaner(self):
        while True:
            try:
                time.sleep(4.9)

                to_delete = []

                now       = time.time()

                self.plugins_lock.acquire()

                for plugin in self.plugins:
                    if now - self.plugins[plugin][2] >= 5. and plugin not in self.hardcoded_plugins:  # 5 seconds timeout
                        to_delete.append(plugin)

                for plugin in to_delete:
                    del self.plugins[plugin]

                self.plugins_lock.release()

            except Exception as e:
                print(f'_plugin_cleaner: failed to clean: {e}')

    def _plugin_command(self, cmd):
        self.mqtt.publish('from/bot/command', cmd)

    def _register_plugin(self, msg):
        self.plugins_lock.acquire()

        try:
            elements = msg.split('|')

            cmd       = None
            descr     = ''
            acl_group = None

            for element in elements:
                k, v = element.split('=')

                if k == 'cmd':
                    cmd = v
                
                elif k == 'descr':
                    descr = v

                elif k == 'agrp':
                    acl_group = v

            if cmd != None:
                if not cmd in self.hardcoded_plugins:
                    if not cmd in self.plugins:
                        print(f'_register_plugin: first announcement of {cmd}')

                    self.plugins[cmd] = [descr, acl_group, time.time()]

                else:
                    print(f'_register_plugin: cannot override "hardcoded" plugin ({cmd})')

            else:
                print(f'_register_plugin: cmd missing in plugin registration')

        except Exception as e:
            print(f'_register_plugin: problem while processing plugin registration "{msg}": {e}')

        self.plugins_lock.release()

    def _recv_msg_cb(self, topic, msg):
        # print(f'irc::_recv_msg_cb: received "{msg}" for topic {topic}')

        topic = topic[len(self.mqtt.get_topix_prefix()):]

        if msg.find('\n') != -1 or msg.find('\r') != -1:
            print(f'irc::_recv_msg_cb: invalid content to send for {topic}')

            return

        if topic == self.topic_privmsg:
            self.send(f'PRIVMSG {self.channel} :{msg}')

        elif topic == self.topic_notice:
            self.send(f'NOTICE {self.channel} :{msg}')

        elif topic == self.topic_topic:
            self.send(f'TOPIC {self.channel} :{msg}')

        elif topic == self.topic_register:
            # print(f'{msg}')
            self._register_plugin(msg)

        else:
            print(f'irc::_recv_msg_cb: invalid topic {topic}')

            return

    def check_acls(self, who, command):
        self.plugins_lock.acquire()

        # "no group" is for everyone
        if command in self.plugins and self.plugins[command][1] == None:
            self.plugins_lock.release()

            return True

        plugin_group = self.plugins[command][1]

        self.plugins_lock.release()

        self.db.probe()  # to prevent those pesky "sever has gone away" problems

        cursor = self.db.db.cursor()

        # check per user ACLs (can override group as defined in plugin)
        cursor.execute('SELECT COUNT(*) FROM acls WHERE command=%s AND who=%s', (command.lower(), who.lower()))

        row = cursor.fetchone()

        if row[0] >= 1:
            return True

        # check per group ACLs (can override group as defined in plugin)
        cursor.execute('SELECT COUNT(*) FROM acls, acl_groups WHERE acl_groups.who=%s AND acl_groups.group_name=acls.who AND command=%s', (who.lower(), command.lower()))

        row = cursor.fetchone()

        if row[0] >= 1:
            return True

        # check if user is in group as specified by plugin
        cursor.execute('SELECT COUNT(*) FROM acl_groups WHERE group_name=%s AND who=%s', (plugin_group, who))

        row = cursor.fetchone()

        if row[0] >= 1:
            return True

        return False

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

            return True

        except Exception as e:
            self.send_error(f'irc::add_acl: failed to insert acl ({e})')

        return False

    def del_acl(self, who, command):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM acls WHERE command=%s AND who=%s LIMIT 1', (command.lower(), who.lower()))

            self.db.db.commit()

            return True

        except Exception as e:
            self.send_error(f'irc::del_acl: failed to delete acl ({e})')
        
        return False

    def forget_acls(self, who):
        match_ = who + '!%'

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM acls WHERE who LIKE %s', (match_,))

            cursor.execute('DELETE FROM acl_groups WHERE who LIKE %s', (match_,))

            self.db.db.commit()

            return True

        except Exception as e:
            self.send_error(f'irc::forget_acls: failed to forget acls for {match_}: {e}')
        
        return False

    def clone_acls(self, from_, to_):
        cursor = self.db.db.cursor()

        try:
            cursor.execute('SELECT group_name FROM acl_groups WHERE who=%s', (from_,))

            for row in cursor.fetchall():
                cursor.execute('INSERT INTO acl_groups(group_name, who) VALUES(%s, %s)', (row, to_))

            self.db.db.commit()

            return None

        except Exception as e:
            return f'failed to clone acls: {e}'
        
        return 'Unexpected situation'

    # new_fullname is the new 'nick!user@host'
    def update_acls(self, who, new_fullname):
        self.db.probe()

        match_ = who + '!%'

        cursor = self.db.db.cursor()

        try:
            cursor.execute('UPDATE acls SET who=%s WHERE who LIKE %s', (new_fullname, match_))

            cursor.execute('UPDATE acl_groups SET who=%s WHERE who LIKE %s', (new_fullname, match_))

            self.db.db.commit()

            return True

        except Exception as e:
            self.send_error(f'irc::update_acls: failed to update acls ({e})')
        
        return False

    def group_add(self, who, group):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('INSERT INTO acl_groups(who, group_name) VALUES(%s, %s)', (who.lower(), group.lower()))

            self.db.db.commit()

            return True

        except Exception as e:
            self.send_error(f'irc::group_add: failed to insert group-member ({e})')

        return False

    def group_del(self, who, group):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM acl_groups WHERE who=%s AND group_name=%s LIMIT 1', (who.lower(), group.lower()))

            self.db.db.commit()

            return True

        except Exception as e:
            self.send_error(f'irc::group-del: failed to delete group-member ({e})')

        return False

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
            self.send_error(f'irc::is_group: failed to query database for group {group} ({e})')

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

        self.send_ok(f'Known commands: {plugins}')

    def add_define(self, command, is_alias, arguments):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('INSERT INTO aliasses(command, is_command, replacement_text) VALUES(%s, %s, %s)', (command.lower(), is_alias, arguments))

            self.db.db.commit()

            return (True, cursor.lastrowid)

        except Exception as e:
            self.send_error(f'irc::add_define: failed to insert alias ({e})')

        return (False, -1)

    def del_define(self, nr):
        self.db.probe()

        cursor = self.db.db.cursor()

        try:
            cursor.execute('DELETE FROM aliasses WHERE nr=%s', (nr,))

            self.db.db.commit()

            return True

        except Exception as e:
            self.send_error(f'irc::del_define: failed to delete alias {nr} ({e})')

        return False

    def check_aliasses(self, text, username):
        parts   = text.split(' ')
        command = parts[0]

        cursor  = self.db.db.cursor()

        cursor.execute('SELECT is_command, replacement_text FROM aliasses WHERE command=%s ORDER BY RAND() LIMIT 1', (command.lower(), ))

        row = cursor.fetchone()

        if row == None:
            return (False, None)

        is_command = row[0]
        repl_text  = row[1]

        space      = text.find(' ')

        query_text = text[space + 1:] if space != -1 else ''

        if is_command:  # initially only replaces command
            text = repl_text + ' ' + query_text

        else:
            text = repl_text

        text = text.replace('%q', query_text)

        exclamation_mark = username.find('!')

        if exclamation_mark != -1:
            username = username[0:exclamation_mark]

        text = text.replace('%u', username)

        if '%m' in text:
            text = text.strip('%m')

            text = '\001ACTION ' + text.strip() + '\001'

        return (is_command, text)

    def invoke_internal_commands(self, prefix, command, splitted_args):
        identifier  = None

        target_type = None

        check_user  = '(not given)'

        if splitted_args != None and len(splitted_args) >= 2:
            if len(splitted_args) >= 3:  # addacl
                target_type = splitted_args[1]

                check_user = splitted_args[2]

            else:
                target_type = None

                check_user = splitted_args[1]

            if check_user in self.users:
                identifier = self.users[check_user]

            elif '!' in check_user:
                identifier = check_user

            elif self.is_group(check_user):
                identifier = check_user

        identifier_is_known = (self.check_user_known(identifier) or self.is_group(identifier)) if identifier != None else False

        if command == 'addacl':
            group_idx = self.find_key_in_list(splitted_args, 'group', 2)

            cmd_idx   = self.find_key_in_list(splitted_args, 'cmd',   2)

            if not identifier_is_known and target_type == 'user':
                self.invoke_who_and_wait(check_user)

                if check_user in self.users:
                    identifier = self.users[check_user]

            if group_idx != None:
                group_name = splitted_args[group_idx + 1]

                if self.group_add(identifier, group_name):  # who, group
                    self.send_ok(f'User {identifier} added to group {group_name}')

                    return self.internal_command_rc.HANDLED

                else:
                    return self.internal_command_rc.ERROR

            elif cmd_idx != None:
                cmd_name = splitted_args[cmd_idx + 1]

                self.plugins_lock.acquire()

                plugin_known = cmd_name in self.plugins

                self.plugins_lock.release()

                if plugin_known:
                    if self.add_acl(identifier, cmd_name):  # who, command
                        self.send_ok(f'ACL added for user {identifier} for command {cmd_name}')

                        return self.internal_command_rc.HANDLED

                    else:
                        return self.internal_command_rc.ERROR

                else:
                    self.send_error(f'ACL added for user {identifier} for command {cmd_name} NOT added: command/plugin not known')

                    return self.internal_command_rc.HANDLED

            else:
                self.send_error(f'Usage: addacl user|group <user|group> group|cmd <group-name|cmd-name>')

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

                if self.group_del(identifier, group_name):  # who, group
                    self.send_ok(f'User {identifier} removed from group {group_name}')

                    return self.internal_command_rc.HANDLED

                else:
                    return self.internal_command_rc.ERROR

            elif cmd_idx != None:
                cmd_name = splitted_args[cmd_idx + 1]

                if self.del_acl(identifier, cmd_name):  # who, command
                    self.send_ok(f'ACL removed for user {identifier} for command {cmd_name}')

                    return self.internal_command_rc.HANDLED

                else:
                    return self.internal_command_rc.ERROR

            else:
                self.send_error(f'Usage: delacl <user> group|cmd <group-name|cmd-name>')

                return self.internal_command_rc.ERROR

        elif command == 'listacls':
            if not identifier_is_known:
                self.invoke_who_and_wait(check_user)

                if check_user in self.users:
                    identifier = self.users[check_user]

            if identifier != None:
                acls = self.list_acls(identifier)

                str_acls = ', '.join(acls)

                self.send_ok(f'ACLs for user {identifier}: "{str_acls}"')

            else:
                self.send_error('Please provide a nick')

            return self.internal_command_rc.HANDLED

        elif command == 'meet':
            if splitted_args != None and len(splitted_args) == 2:
                user_to_update = splitted_args[1]

                self.invoke_who_and_wait(user_to_update)

                if user_to_update in self.users:
                    self.update_acls(user_to_update, self.users[user_to_update])

                    self.send_ok(f'User {user_to_update} updated to {self.users[user_to_update]}')

                else:
                    self.send_error(f'User {user_to_update} is not known')

            else:
                self.send_error(f'Meet parameter missing ({splitted_args} given)')

        elif command == 'commands':
            self.list_plugins()

            return self.internal_command_rc.HANDLED

        elif command == 'define' or command == 'alias':
            if len(splitted_args) >= 3:
                self.plugins_lock.acquire()

                plugin_known = splitted_args[1] in self.plugins

                self.plugins_lock.release()

                if plugin_known:
                    self.send_error(f'Cannot override internal/plugin commands')

                else:
                    rc, nr = self.add_define(splitted_args[1], command == 'alias', ' '.join(splitted_args[2:]))

                    if rc == True:
                        self.send_ok(f'{command} added (number: {nr})')

                    else:
                        self.send_error(f'Failed to add {command}')

            else:
                self.send_error(f'{command} missing arguments')

        elif command == 'deldefine':
            if len(splitted_args) == 2:
                nr = splitted_args[1]

                rc = self.del_define(nr)

                if rc == True:
                    self.send_ok(f'Define {nr} deleted')

                else:
                    self.send_error(f'Failed to delete {nr}')

            else:
                self.send_error(f'{command} missing arguments')

        elif command == 'help':
            if len(splitted_args) == 2:
                cmd = splitted_args[1]

                self.plugins_lock.acquire()

                if cmd in self.plugins:
                    self.send_ok(f'Command {cmd}: {self.plugins[cmd][0]} (group: {self.plugins[cmd][1]})')

                else:
                    self.send_error(f'Command/plugin not known')

                self.plugins_lock.release()

            else:
                self.list_plugins()

            return self.internal_command_rc.HANDLED

        elif command == 'more':
            self.send_more()

            return self.internal_command_rc.HANDLED

        elif command == 'forget':
            if len(splitted_args) == 2:
                user = splitted_args[1]

                if self.forget_acls(user):
                    self.send_ok(f'User {user} forgotten')

                else:
                    self.send_error(f'User {user} not known or some other error')

            else:
                self.send_error(f'User not specified')

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
                        self.send_ok(f'User {from_} cloned (to {to_})')

                    else:
                        self.send_error(f'Cannot clone {from_} to {to_}: {error}')

                else:
                    self.send_error(f'Either {from_} or {to_} is unknown')

            else:
                self.send_error(f'User "from" and/or "to" not specified')

            return self.internal_command_rc.HANDLED

        return self.internal_command_rc.NOT_INTERNAL
    
    def irc_command_insertion_point(self, prefix, command, arguments):
        if command in [ 'JOIN', 'PART', 'KICK', 'NICK' ]:
            self.mqtt.publish(f'from/irc/{self.channel[1:]}/{prefix}/{command}', ' '.join(arguments))

        return True

# host, user, password, database
db = dbi('localhost', 'ghbot', 'yourmum', 'ghbot')

# broker_ip, topic_prefix
m = mqtt_handler('mqtt.vm.nurd.space', 'GHBot/')

# host, port, nick, channel, m, db, command_prefix
i = ghbot('irc.oftc.net', 6667, 'ghbot', '#nurdbottest', m, db, '~')

ka = irc_keepalive(i)

print('Go!')

while True:
    time.sleep(1.)

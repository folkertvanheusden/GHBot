#! /usr/bin/python3

from enum import Enum
import MySQLdb
import paho.mqtt.client as mqtt
import select
import socket
import sys
from threading import Thread
import time
import traceback


class irc(Thread):
    class session_state(Enum):
        DISCONNECTED   = 0x00  # setup socket, connect to host
        CONNECTED_NICK = 0x02  # send NICK
        CONNECTED_USER = 0x03  # send USER
        USER_WAIT      = 0x08  # wait for USER ack
        CONNECTED_JOIN = 0x10  # send JOIN
        CONNECTED_WAIT = 0x11  # wait for 'JOIN' indicating that the JOIN succeeded
        RUNNING        = 0xf0  # go
        DISCONNECTING  = 0xff

    class internal_command_rc(Enum):
        HANDLED      = 0x00
        ERROR        = 0x10
        NOT_INTERNAL = 0xff

    state_timeout = 30         # state changes must not take longer than this

    def __init__(self, host, port, nick, channel, m, db, cmd_prefix):
        super().__init__()

        self.cmd_prefix  = cmd_prefix

        self.db          = db

        self.mqtt        = m

        self.topic_privmsg = f'to/irc/{channel[1:]}/privmsg'  # Send reply in channel via PRIVMSG
        self.topic_notice  = f'to/irc/{channel[1:]}/notice'   # Send reply in channel via NOTICE
        self.topic_topic   = f'to/irc/{channel[1:]}/topic'    # Sets TOPIC for channel

        self.mqtt.subscribe(self.topic_privmsg, self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_notice,  self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_topic,   self._recv_msg_cb)

        self.host        = host
        self.port        = port
        self.nick        = nick
        self.channel     = channel

        self.fd          = None

        self.state       = self.session_state.DISCONNECTED
        self.state_since = time.time()

        self.users       = dict()

        self.start()

    def _set_state(self, s):
        print(f'_set_state: state changes from {self.state} to {s}')

        self.state = s

        self.state_since = time.time()

    def _recv_msg_cb(self, topic, msg):
        print(f'irc::_recv_msg_cb: received "{msg}" for topic {topic}')

        if msg.find('\n') != -1 or msg.find('\r') != -1:
            print(f'irc::_recv_msg_cb: invalid content to send for {topic}')

            return

        if topic == self.topic_privmsg:
            self.send(f'PRIVMSG {self.channel} :{msg}')

        elif topic == self.topic_notice:
            self.send(f'NOTICE {self.channel} :{msg}')

        elif topic == self.topic_topic:
            self.send(f'TOPIC {self.channel} :{msg}')

        else:
            print(f'irc::_recv_msg_cb: invalid topic {topic}')

            return

    def send(self, s):
        try:
            self.fd.send(f'{s}\r\n'.encode('ascii'))

            return True

        except Exception as e:
            print(f'irc::send: failed transmitting to IRC server: {e}')

            self.fd.close()

            self._set_state(self.session_state.DISCONNECTED)

        return False

    def send_ok(self, text):
        print(f'OK: {text}')

        self.send(f'PRIVMSG {self.channel} :{text}')

    def send_error(self, text):
        print(f'ERROR: {text}')

        self.send(f'PRIVMSG {self.channel} :ERROR: {text}')

    def parse_irc_line(self, s):
        # from https://stackoverflow.com/questions/930700/python-parsing-irc-messages

        prefix = ''
        trailing = []

        if s[0] == ':':
            prefix, s = s[1:].split(' ', 1)

        if s.find(' :') != -1:
            s, trailing = s.split(' :', 1)

            args = s.split()
            args.append(trailing)

        else:
            args = s.split()

        command = args.pop(0)

        return prefix, command, args

    def check_acls(self, who, command):
        self.db.probe()  # to prevent those pesky "sever has gone away" problems

        cursor = self.db.db.cursor()

        # check per user ACLs
        cursor.execute('SELECT COUNT(*) FROM acls WHERE command=%s AND who=%s LIMIT 1', (command.lower(), who.lower()))

        row = cursor.fetchone()

        if row[0] == 1:
            return True

        cursor.execute('SELECT COUNT(*) FROM acls, acl_groups WHERE acl_groups.who=%s AND acl_groups.group_name=acls.who AND command=%s LIMIT 1', (who.lower(), command.lower()))

        row = cursor.fetchone()

        if row[0] == 1:
            return True

        return False

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

    def invoke_internal_commands(self, prefix, command, args):
        splitted_args = None

        if len(args) == 2:
            splitted_args = args[1].split(' ')

        identifier = None

        if splitted_args != None and len(splitted_args) >= 2:
            if splitted_args[1] in self.users:
                identifier = self.users[splitted_args[1]]

            elif '!' in splitted_args[1]:
                identifier = splitted_args[1]

            elif self.is_group(splitted_args[1]):
                identifier = splitted_args[1]

        identifier_is_known = (self.check_user_known(identifier) or self.is_group(identifier)) if identifier != None else False

        if command == 'addacl':
            if splitted_args != None and len(splitted_args) == 3:
                if identifier_is_known:
                    if self.add_acl(identifier, splitted_args[2]):  # who, command
                        self.send_ok(f'ACL added for user {splitted_args[1]}')

                        return self.internal_command_rc.HANDLED

                    else:
                        return self.internal_command_rc.ERROR

                else:
                    self.send_error(f'User {splitted_args[1]} not known, use "meet"')

                    return self.internal_command_rc.HANDLED

            else:
                self.send_error(f'irc::invoke_internal_commands: addacl parameter(s) missing ({splitted_args} given)')

                return self.internal_command_rc.ERROR

            return False

        elif command == 'delacl':
            if splitted_args != None and len(splitted_args) == 3:
                if identifier_is_known:
                    if self.del_acl(identifier, splitted_args[2]):  # who, command
                        self.send_ok(f'ACL deleted from user {splitted_args[1]}')

                        return self.internal_command_rc.HANDLED

                    else:
                        return self.internal_command_rc.ERROR

                else:
                    self.send_error(f'User {splitted_args[1]} not known, use "meet"')

                    return self.internal_command_rc.HANDLED

            else:
                self.send_error(f'irc::invoke_internal_commands: addacl parameter(s) missing ({splitted_args} given)')

                return self.internal_command_rc.ERROR

        elif command == 'groupadd':
            if splitted_args != None and len(splitted_args) == 3:
                if identifier_is_known:
                    if self.group_add(identifier, splitted_args[2]):  # who, group
                        self.send_ok(f'User {splitted_args[1]} added to ACL-group')

                        return self.internal_command_rc.HANDLED

                    else:
                        return self.internal_command_rc.ERROR

                else:
                    self.send_error(f'User {splitted_args[1]} not known, use "meet"')

                    return self.internal_command_rc.HANDLED

            else:
                self.send_error(f'irc::invoke_internal_commands: groupadd parameter(s) missing ({splitted_args} given)')

                return self.internal_command_rc.ERROR

        elif command == 'groupdel':
            if splitted_args != None and len(splitted_args) == 3:
                if identifier_is_known:
                    if self.group_del(identifier, splitted_args[2]):  # who, group
                        self.send_ok(f'User {splitted_args[1]} removed from ACL-group')

                        return self.internal_command_rc.HANDLED

                    else:
                        return self.internal_command_rc.ERROR

                else:
                    self.send_error(f'User {splitted_args[1]} not known, use "meet"')

                    return self.internal_command_rc.HANDLED

            else:
                self.send_error(f'irc::invoke_internal_commands: groupdel parameter(s) missing ({splitted_args} given)')

                return self.internal_command_rc.ERROR

        elif command == 'meet':
            if splitted_args != None and len(splitted_args) == 2:
                self.send(f'WHO {splitted_args[1]}')

            else:
                self.send_error(f'irc::invoke_internal_commands: meet parameter missing ({splitted_args} given)')

        return self.internal_command_rc.NOT_INTERNAL

    def handle_irc_commands(self, prefix, command, args):
        print(prefix, '|', command, '|', args)

        if len(command) == 3 and command.isnumeric():
            if command == '001':
                if self.state == self.session_state.USER_WAIT:
                    self._set_state(self.session_state.CONNECTED_JOIN)

                else:
                    print(f'irc::run: invalid state for "001" command {self.state}')

                    self._set_state(self.session_state.DISCONNECTING)

            elif command == '352':  # reponse to 'WHO'
                self.users[args[5]] = f'{args[5]}!{args[2]}@{args[3]}'

                print(f'{args[5]} is {self.users[args[5]]}')

            elif command == '353':  # users in the channel
                for user in args[3].split(' '):
                    self.users[user] = '?'

        elif command == 'JOIN':
            if self.state == self.session_state.CONNECTED_WAIT:
                self._set_state(self.session_state.RUNNING)

            self.users[prefix.split('!')[0]] = prefix.lower()

        elif command == 'PART':
            del self.users[prefix.split('!')[0]]

        elif command == 'KICK':
            del self.users[args[1]]

        elif command == 'NICK':
            lower_prefix = prefix.lower()

            del self.users[user]

            self.users[args[0]] = lower_prefix

        elif command == 'PING':
            if len(args) >= 1:
                self.send(f'PONG {args[0]}')

            else:
                self.send(f'PONG')

        elif command == 'PRIVMSG':
            if len(args) >= 2 and len(args[1]) >= 2:
                if args[1][0] == self.cmd_prefix:
                    command = args[1][1:].split(' ')[0]

                    if self.check_acls(prefix, command):
                        # returns False when the command is not internal
                        rc = self.invoke_internal_commands(prefix, command, args)

                        if rc == self.internal_command_rc.HANDLED:
                            pass

                        elif rc == self.internal_command_rc.NOT_INTERNAL:
                            self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/{command}', args[1])

                        elif rc == self.internal_command_rc.ERROR:
                            pass

                        else:
                            self.send_error(f'irc::run: unexpected return code from internal commands handler ({rc})')

                    else:
                        self.send_error(f'irc::run: Command "{command}" denied for user "{prefix}"')

                else:
                    self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/message', args[1])

        elif command == 'NOTICE':
            if len(args) >= 2:
                self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/notice', args[1])

        else:
            print(f'irc::run: command "{command}" is not known (for {prefix})')

    def run(self):
        print('irc::run: started')

        buffer = ''

        while True:
            if self.state == self.session_state.DISCONNECTING:
                self.fd.close()

                self._set_state(self.session_state.DISCONNECTED)

            elif self.state == self.session_state.DISCONNECTED:
                self.fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                print(f'irc::run: connecting to [{self.host}]:{self.port}')

                try:
                    self.fd.connect((self.host, self.port))

                    self.poller = select.poll()

                    self.poller.register(self.fd, select.POLLIN)

                    self._set_state(self.session_state.CONNECTED_NICK)

                except Exception as e:
                    self.send_error(f'irc::run: failed to connect: {e}')
                    
                    self.fd.close()

            elif self.state == self.session_state.CONNECTED_NICK:
                # apparently only error responses are returned, no acks
                if self.send(f'NICK {self.nick}'):
                    self._set_state(self.session_state.CONNECTED_USER)

            elif self.state == self.session_state.CONNECTED_USER:
                if self.send(f'USER {self.nick} 0 * :{self.nick}'):
                    self._set_state(self.session_state.USER_WAIT)

            elif self.state == self.session_state.CONNECTED_JOIN:
                if self.send(f'JOIN {self.channel}'):
                    self._set_state(self.session_state.CONNECTED_WAIT)

            elif self.state == self.session_state.USER_WAIT:
                # handled elsewhere
                pass

            elif self.state == self.session_state.CONNECTED_WAIT:
                # handled elsewhere
                pass

            elif self.state == self.session_state.RUNNING:
                pass

            else:
                print(f'irc::run: internal error, invalid state {self.state}')

            if self.state != self.session_state.DISCONNECTED and (len(buffer) > 0 or len(self.poller.poll(100)) > 0):
                lf_index = buffer.find('\n')

                if lf_index == -1:
                    try:
                        buffer += self.fd.recv(4096).decode('ascii')

                    except Exception as e:
                        self.send_error(f'irc::run: cannot decode text from irc-server')

                    lf_index = buffer.find('\n')

                    if lf_index == -1:
                        continue

                line = buffer[0:lf_index].rstrip('\r').strip()
                buffer = buffer[lf_index + 1:]

                prefix, command, arguments = self.parse_irc_line(line)

                try:
                    self.handle_irc_commands(prefix, command, arguments)

                except Exception as e:
                    self.send_error(f'irc::run: exception "{e}" during execution of IRC command "{command}"')

                    traceback.print_exc(file=sys.stdout)

            if not self.state in [ self.session_state.DISCONNECTED, self.session_state.DISCONNECTING, self.session_state.RUNNING ]:
                takes = time.time() - self.state_since

                if takes > irc.state_timeout:
                    print(f'irc::run: state {self.state} timeout ({takes} > {irc.state_timeout})')

                    self._set_state(self.session_state.DISCONNECTING)

class mqtt_handler(Thread):
    def __init__(self, broker_ip):
        super().__init__()

        self.client = mqtt.Client()

        self.topics = []

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.client.connect(broker_ip, 1883, 60)

        self.start()

    def subscribe(self, topic, msg_recv_cb):
        print(f'mqtt_handler::topic: subscribe to {topic}')

        self.topics.append((topic, msg_recv_cb))

        self.client.subscribe(topic)

    def publish(self, topic, content):
        print(f'mqtt_handler::topic: publish "{content}" to "{topic}"')

        self.client.publish(topic, content)

    def on_connect(self, client, userdata, flags, rc):
        for topic in self.topics:
            print(f'mqtt_handler::topic: re-subscribe to {topic[0]}')

            self.client.subscribe(topic[0])

    def on_message(self, client, userdata, msg):
        print(f'mqtt_handler::topic: received "{msg.payload}" in topic "{msg.topic}"')

        for topic in self.topics:
            if topic[0] == msg.topic:
                topic[1](msg.topic, msg.payload.decode('ascii'))

                return

        print(f'mqtt_handler::topic: no handler for topic "{msg.topic}"')

    def run(self):
        while True:
            print('mqtt_handler::run: looping')

            self.client.loop_forever()

class dbi(Thread):
    def __init__(self, host, user, password, database):
        super().__init__()

        self.host = host
        self.user = user
        self.password = password
        self.database = database

        self.reconnect()

        self.start()

    def reconnect(self):
        self.db = MySQLdb.connect(self.host, self.user, self.password, self.database)

    def probe(self):
        try:
            cursor = self.db.cursor()

            cursor.execute('SELECT NOW()')

            cursor.fetchone()

        except Exception as e:
            print(f'MySQL indicated error: {e}')

            self.reconnect()

    def run(self):
        while True:
            self.probe()

            time.sleep(29)

db = dbi('mauer', 'ghbot', 'ghbot', 'ghbot')

m = mqtt_handler('192.168.64.1')

i = irc('192.168.64.1', 6667, 'ghbot', '#test', m, db, '~')

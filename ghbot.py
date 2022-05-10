#! /usr/bin/python3

from enum import IntFlag
import MySQLdb
import paho.mqtt.client as mqtt
import select
import socket
from threading import Thread


class irc(Thread):
    class session_state(IntFlag):
        DISCONNECTED   = 0x00  # setup socket, connect to host
        CONNECTED_NICK = 0x02  # send NICK
        CONNECTED_USER = 0x03  # send USER
        CONNECTED_JOIN = 0x10  # send JOIN
        RUNNING        = 0xf0  # go
        DISCONNECTING  = 0xff

    def __init__(self, host, port, nick, channel, m, db):
        super().__init__()

        self.db      = db

        self.mqtt    = m

        self.topic_privmsg = f'to/irc/{channel[1:]}/privmsg'  # Send reply in channel via PRIVMSG
        self.topic_notice  = f'to/irc/{channel[1:]}/notice'   # Send reply in channel via NOTICE
        self.topic_topic   = f'to/irc/{channel[1:]}/topic'    # Sets TOPIC for channel

        self.mqtt.subscribe(self.topic_privmsg, self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_notice,  self._recv_msg_cb)
        self.mqtt.subscribe(self.topic_topic,   self._recv_msg_cb)

        self.host    = host
        self.port    = port
        self.nick    = nick
        self.channel = channel

        self.fd      = None
        self.state   = self.session_state.DISCONNECTED

        self.start()

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

            self.state = self.session_state.DISCONNECTED

        return False

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
        cursor = self.db.cursor()

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

    def handle_irc_commands(self, prefix, command, args):
        if len(command) == 3 and command.isnumeric():
            if command == '001':
                if self.state == self.session_state.CONNECTED_USER:
                    self.state = self.session_state.CONNECTED_JOIN

                else:
                    print(f'irc::run: invalid state for "001" command {self.state}')

                    self.state = self.session_state.DISCONNECTING

        elif command == 'PING':
            if len(args) >= 2:
                self.send(f'PONG {args[1]}')

            else:
                self.send(f'PONG')

        elif command == 'PRIVMSG':
            if len(args) >= 2:
                if args[1][0] == '#':
                    command = args[1][1:].split(' ')[0]

                    if self.check_acls(prefix, command):
                        self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/{command}', args[1])

                    else:
                        print(f'irc::run: Command "{command}" denied for user "{prefix}"')

                else:
                    self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/message', args[1])

        else:
            print(f'irc::run: command "{command}" is not known')

    def run(self):
        print('irc::run: started')

        buffer = ''

        while True:
            if self.state == self.session_state.DISCONNECTING:
                self.fd.close()

                self.state = self.session_state.DISCONNECTED

            elif self.state == self.session_state.DISCONNECTED:
                self.fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                print(f'irc::run: connecting to [{self.host}]:{self.port}')

                try:
                    self.fd.connect((self.host, self.port))

                    self.poller = select.poll()

                    self.poller.register(self.fd, select.POLLIN)

                    self.state = self.session_state.CONNECTED_NICK

                except Exception as e:
                    print(f'irc::run: failed to connect: {e}')
                    
                    self.fd.close()

            elif self.state == self.session_state.CONNECTED_NICK:
                # apparently only error responses are returned, no acks
                if self.send(f'NICK {self.nick}'):
                    self.state = self.session_state.CONNECTED_USER

            elif self.state == self.session_state.CONNECTED_USER:
                self.send(f'USER {self.nick} 0 * :{self.nick}')

            elif self.state == self.session_state.CONNECTED_JOIN:
                if self.send(f'JOIN {self.channel}'):
                    self.state = self.session_state.RUNNING

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
                        print(f'irc::run: cannot decode text from irc-server')

                    lf_index = buffer.find('\n')

                    if lf_index == -1:
                        continue

                line = buffer[0:lf_index].rstrip('\r')
                buffer = buffer[lf_index + 1:]

                print(line)
                prefix, command, arguments = self.parse_irc_line(line)

                self.handle_irc_commands(prefix, command, arguments)

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

db = MySQLdb.connect('mauer', 'ghbot', 'ghbot', 'ghbot')

m = mqtt_handler('192.168.64.1')

i = irc('192.168.64.1', 6667, 'ghbot', '#test', m, db)

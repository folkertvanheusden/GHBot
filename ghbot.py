#! /usr/bin/python3

from enum import IntFlag
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

    def __init__(self, host, port, nick, channel):
        super().__init__()

        self.host    = host
        self.port    = port
        self.nick    = nick
        self.channel = channel

        self.fd      = None
        self.state   = self.session_state.DISCONNECTED

        self.start()

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

    def handle_irc_commands(self, prefix, command, args):
        if len(command) == 3 and command.isnumeric():
            if command == '001':
                if self.state == self.session_state.CONNECTED_USER:
                    self.state = self.session_state.CONNECTED_JOIN

                else:
                    print(f'irc::run: invalid state for "001" command {self.state}')

                    self.state = self.session_state.DISCONNECTING

        elif command == 'PING':
            self.send(f'PONG {arguments[0]}')

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

            if self.state != self.session_state.DISCONNECTED and len(self.poller.poll(100)) > 0:
                buffer += self.fd.recv(4096).decode('ascii').strip('\r')

                lf_index = buffer.find('\n')

                if lf_index == -1:
                    continue

                line = buffer[0:lf_index]
                buffer = buffer[lf_index + 1:]

                print(line)
                prefix, command, arguments = self.parse_irc_line(line)

                self.handle_irc_commands(prefix, command, arguments)

i = irc('192.168.64.1', 6667, 'ghbot', '#test')

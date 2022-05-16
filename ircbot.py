#! /usr/bin/python3

from enum import Enum
import math
import select
import socket
import sys
import threading
import time
import traceback


class ircbot(threading.Thread):
    class session_state(Enum):
        DISCONNECTED   = 0x00  # setup socket, connect to host
        CONNECTED_NICK = 0x02  # send NICK
        CONNECTED_USER = 0x03  # send USER
        USER_WAIT      = 0x08  # wait for USER ack
        CONNECTED_JOIN = 0x10  # send JOIN
        CONNECTED_WAIT = 0x11  # wait for 'JOIN' indicating that the JOIN succeeded
        RUNNING        = 0xf0  # go
        DISCONNECTING  = 0xff

    state_timeout = 30         # state changes must not take longer than this

    def __init__(self, host, port, nick, channel):
        super().__init__()

        self.host        = host
        self.port        = port
        self.nick        = nick
        self.channel     = channel

        self.fd          = None

        self.state       = self.session_state.DISCONNECTED
        self.state_since = time.time()

        self.users       = dict()

        self.cond_352    = threading.Condition()

        self.more        = ''

    def _set_state(self, s):
        print(f'_set_state: state changes from {self.state} to {s}')

        self.state = s

        self.state_since = time.time()

    def get_state(self):
        return self.state

    def send(self, s):
        try:
            self.fd.send(f'{s}\r\n'.encode('utf-8'))

            return True

        except Exception as e:
            print(f'irc::send: failed transmitting to IRC server: {e}')

            self.fd.close()

            self._set_state(self.session_state.DISCONNECTED)

        return False

    def send_ok(self, text):
        print(f'OK: {text}')

        # 200 is arbitrary: does the irc server give a hint on this value?
        if len(text) > 200:
            self.more = text[200:]

            n = math.ceil(len(self.more) / 200)

            self.send(f'PRIVMSG {self.channel} :{text[0:200]} ({n} ~more)')

        else:
            self.send(f'PRIVMSG {self.channel} :{text}')

            self.more = ''

    def send_more(self):
        if self.more == '':
            self.send(f'PRIVMSG {self.channel} :No more ~more')

        else:
            current_more = self.more[0:200]

            if len(self.more) > 200:
                self.more = self.more[200:]

            else:
                self.more = ''

            n = math.ceil(len(self.more) / 200)

            self.send(f'PRIVMSG {self.channel} :{current_more} ({n} ~more)')

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

    def invoke_who_and_wait(self, user):
        self.send(f'WHO {user}')

        start = time.time()

        while self.check_user_known(user) == False:
            t_diff = time.time() - start

            if t_diff >= 5.0:
                break

            with self.cond_352:
                self.cond_352.wait(5.0 - t_diff)

    def invoke_internal_commands(self, prefix, command, splitted_args):
        return self.internal_command_rc.NOT_INTERNAL

    def handle_irc_commands(self, prefix, command, args):
        if command != 'PING':
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

            # 315 is 'end of who'
            if command == '352' or command == '315':
                with self.cond_352:
                    self.cond_352.notify_all()

        elif command == 'JOIN':
            if self.state == self.session_state.CONNECTED_WAIT:
                self._set_state(self.session_state.RUNNING)

            self.users[prefix.split('!')[0]] = prefix.lower()

        elif command == 'PART':
            del self.users[prefix.split('!')[0]]

        elif command == 'KICK':
            del self.users[args[1]]

        elif command == 'NICK':
            old_lower_prefix = prefix.lower()

            excl_mark    = old_lower_prefix.find('!')

            old_user     = old_lower_prefix[0:excl_mark]

            del self.users[old_user]
        
            new_user     = args[0]

            new_prefix   = new_user + old_lower_prefix[excl_mark:]

            self.users[new_user] = new_prefix

            print(f'{old_lower_prefix} => {new_prefix}')

        elif command == 'PING':
            if len(args) >= 1:
                self.send(f'PONG {args[0]}')

            else:
                self.send(f'PONG')

        elif command == 'PRIVMSG':
            if len(args) >= 2 and len(args[1]) >= 2:
                text = args[1]

                if text[0] == self.cmd_prefix:
                    is_command, new_text = self.check_aliasses(text[1:], prefix)

                    if new_text != None:
                        if not is_command:
                            self.send_ok(new_text)

                            return

                        text = self.cmd_prefix + new_text

                if text[0] == self.cmd_prefix:
                    parts   = text[1:].split(' ')

                    command = parts[0]

                    if not command in self.plugins:
                        self.send_error(f'Command "{command}" is not known')

                    elif self.check_acls(prefix, command):
                        # returns False when the command is not internal
                        rc = self.invoke_internal_commands(prefix, command, parts)

                        if rc == self.internal_command_rc.HANDLED:
                            pass

                        elif rc == self.internal_command_rc.NOT_INTERNAL:
                            self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/{command}', text)

                        elif rc == self.internal_command_rc.ERROR:
                            pass

                        else:
                            self.send_error(f'irc::run: unexpected return code from internal commands handler ({rc})')

                    else:
                        self.send_error(f'Command "{command}" denied for user "{prefix}"')

                else:
                    self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/message', args[1])

        elif command == 'NOTICE':
            if len(args) >= 2:
                self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/notice', args[1])

        else:
            print(f'irc::run: command "{command}" is not known (for {prefix})')

    def handle_irc_command_thread_wrapper(self, prefix, command, arguments):
        try:
            self.handle_irc_commands(prefix, command, arguments)

        except Exception as e:
            self.send_error(f'irc::handle_irc_command_thread_wrapper: exception "{e}" during execution of IRC command "{command}"')

            traceback.print_exc(file=sys.stdout)

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
                        buffer += self.fd.recv(4096).decode('utf-8')

                    except Exception as e:
                        self.send_error(f'irc::run: cannot decode text from irc-server')

                    lf_index = buffer.find('\n')

                    if lf_index == -1:
                        continue

                line = buffer[0:lf_index].rstrip('\r').strip()
                buffer = buffer[lf_index + 1:]

                prefix, command, arguments = self.parse_irc_line(line)

                t = threading.Thread(target=self.handle_irc_command_thread_wrapper, args=(prefix, command, arguments), daemon=True)
                t.name = 'GHBot input'
                t.start()

            if not self.state in [ self.session_state.DISCONNECTED, self.session_state.DISCONNECTING, self.session_state.RUNNING ]:
                takes = time.time() - self.state_since

                if takes > ircbot.state_timeout:
                    print(f'irc::run: state {self.state} timeout ({takes} > {ircbot.state_timeout})')

                    self._set_state(self.session_state.DISCONNECTING)

class irc_keepalive(threading.Thread):
    def __init__(self, i):
        super().__init__()

        self.i = i

        self.name = 'GHBot keepalive'
        self.start()

    def run(self):
        while True:
            try:
                if self.i.get_state() == ircbot.session_state.RUNNING:
                    self.i.send('TIME')

                    time.sleep(30)

                else:
                    time.sleep(5)

            except Exception as e:
                print(f'irc_keepalive::run: exception {e}')

                time.sleep(1)

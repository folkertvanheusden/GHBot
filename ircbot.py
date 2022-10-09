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

    def __init__(self, host, port, nick, channels):
        super().__init__()

        self.host        = host
        self.port        = port
        self.nick        = nick
        self.channels    = channels

        self.joined_ch   = dict()

        self.fd          = None

        self.owner       = 'flok'

        self.state       = self.session_state.DISCONNECTED
        self.state_since = time.time()

        self.users       = dict()

        self.cond_352    = threading.Condition()

        self.more        = dict()

        self.topics      = dict()

        for channel in channels:
            self.more[channel] = ''

            self.joined_ch[channel] = False

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

    # TODO: more
    def send_notice(self, channel, text):
        if channel[0] == '\\':
            channel = channel[1:]

        self.send(f'NOTICE {channel} :{text}')

    def send_ok(self, channel, text):
        print(f'OK: {channel}|{text}')

        if len(text) > 350:
            self.more[channel] = text

            self.send_more(channel)

        else:
            if channel[0] == '\\':
                channel = channel[1:]

            self.send(f'PRIVMSG {channel} :{text}')

            self.more[channel] = ''

    def send_more(self, channel):
        if not channel in self.more or self.more[channel] == '':
            if channel[0] == '\\':
                channel = channel[1:]

            self.send(f'PRIVMSG {channel} :No more ~more')

        else:
            space = self.more[channel].find(' ', 300, 350)

            if space == -1:
                space = 325

            current_more = self.more[channel][0:space].strip()

            if len(self.more[channel]) > space:
                self.more[channel] = self.more[channel][space:].strip()

            else:
                self.more[channel] = ''

            n = math.ceil(len(self.more[channel]) / 350)

            if channel[0] == '\\':
                channel = channel[1:]

            self.send(f'PRIVMSG {channel} :{current_more} ({n} ~more)')

    # TODO: more
    def send_error(self, channel, text):
        if channel[0] == '\\':
            channel = channel[1:]

        print(f'ERROR: {channel}|{text}')

        self.send(f'PRIVMSG {channel} :ERROR: {text}')

    # TODO: more
    def send_error_notice(self, channel, text):
        print(f'ERROR: {channel}|{text}')

        if channel[0] == '\\':
            channel = channel[1:]

        self.send(f'NOTICE {channel} :ERROR: {text}')

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

    def invoke_internal_commands(self, prefix, command, splitted_args, channel):
        return self.internal_command_rc.NOT_INTERNAL

    def handle_irc_commands(self, prefix, command, args):
        if len(command) == 3 and command.isnumeric():
            if command == '001':
                if self.state == self.session_state.USER_WAIT:
                    self._set_state(self.session_state.CONNECTED_JOIN)

                else:
                    print(f'irc::run: invalid state for "001" command {self.state}')

                    self._set_state(self.session_state.DISCONNECTING)

            elif command == '352':  # reponse to 'WHO'
                #print(prefix, command, args)
                self.users[args[5].lower()] = f'{args[5]}!{args[2]}@{args[3]}'

            elif command == '353':  # users in the channel
                for user in args[3].split(' '):
                    self.users[user.lower()] = '?'

            elif command == '331' or command == '332':  # no topic set / topic
                self.topics[args[1][1:]] = args[2]

                self.mqtt.publish(f'from/irc/{args[1][1:]}/topic', args[2])

            # 315 is 'end of who'
            if command == '352' or command == '315':
                with self.cond_352:
                    self.cond_352.notify_all()

        elif command == 'JOIN':
            if self.state == self.session_state.CONNECTED_WAIT:
                self.joined_ch[args[0]] = True

                all_joined = True

                for channel in self.joined_ch:
                    if self.joined_ch[channel] == False:
                        all_joined = False

                        break

                if all_joined:
                    self._set_state(self.session_state.RUNNING)

            self.users[prefix.split('!')[0].lower()] = prefix.lower()

        elif command == 'PART' or command == 'QUIT':
            #print(prefix, command)
            nick = prefix.split('!')[0].lower()

            if nick in self.users:
                del self.users[nick]

        elif command == 'KICK':
            nick = args[1].lower()

            if nick in self.users:
                del self.users[nick]

        elif command == 'NICK':
            try:
                old_lower_prefix = prefix.lower()

                excl_mark    = old_lower_prefix.find('!')

                old_user     = old_lower_prefix[0:excl_mark]

                if old_user in self.users:
                    del self.users[old_user]
            
                new_user     = args[0].lower()

                new_prefix   = new_user + old_lower_prefix[excl_mark:]

                self.users[new_user] = new_prefix

                #print(f'{old_lower_prefix} => {new_prefix}')

            except Exception as e:
                send_notice(self.owner, f'irc::handle_irc_command: exception "{e}" during execution of IRC command NICK at line number: {e.__traceback__.tb_lineno}')

        elif command == 'PING':
            if len(args) >= 1:
                self.send(f'PONG {args[0]}')

            else:
                self.send(f'PONG')

        elif command == 'PRIVMSG':
            #print(args)

            if len(args) >= 2 and len(args[1]) >= 2:
                channel = args[0]
                text    = args[1]

                if text[0] == self.cmd_prefix:
                    is_command, new_text, is_notice = self.check_aliasses(text[1:], prefix)

                    if new_text != None:
                        if not is_command:
                            if is_notice:
                                self.send_notice(channel, new_text)

                            else:
                                self.send_ok(channel, new_text)

                            return

                        text = self.cmd_prefix + new_text

                if text[0] == self.cmd_prefix:
                    parts   = text[1:].split(' ')

                    command = parts[0]

                    if not command in self.plugins:
                        nick = prefix.split('!')[0].lower()

                        method = self.send_error_notice

                        if channel == self.nick:
                            channel = prefix

                            if '!' in channel:
                                channel = channel[0:channel.find('!')]

                            method = self.send_error

                        if command in self.plugins_gone:
                            method(channel, f'{nick}: command "{command}" is unresponsive for {time.time() - self.plugins_gone[command]:.2f} seconds')

                        else:
                            method(channel, f'{nick}: command "{command}" is not known')

                    else:
                        access_granted, group_for_command = self.check_acls(prefix, command)

                        response_channel = (prefix[0:prefix.find('!')] if '!' in prefix else prefix) if channel == self.nick else channel

                        if access_granted:
                            # returns False when the command is not internal
                            rc = self.invoke_internal_commands(prefix, command, parts, channel)

                            if rc == self.internal_command_rc.HANDLED:
                                pass

                            elif rc == self.internal_command_rc.NOT_INTERNAL:
                                if channel == self.nick:
                                    person = prefix

                                    if '!' in person:
                                        person = person[0:person.find('!')]

                                    self.mqtt.publish(f'from/irc/\\{person}/{prefix}/{command}', text)

                                else:
                                    self.mqtt.publish(f'from/irc/{channel[1:]}/{prefix}/{command}', text)

                            elif rc == self.internal_command_rc.ERROR:
                                pass

                            else:
                                self.send_error(response_channel, f'irc::run: unexpected return code from internal commands handler ({rc})')

                        else:
                            self.send_error(response_channel, f'Command "{command}" denied for user "{prefix}", one must be in {group_for_command}')

                else:
                    self.mqtt.publish(f'from/irc/{channel[1:]}/{prefix}/message', args[1])

        elif command == 'NOTICE':
            if len(args) >= 2:
                self.mqtt.publish(f'from/irc/{args[0][1:]}/{prefix}/notice', args[1])

        elif command == 'TOPIC':
            self.topics[args[0][1:]] = args[1]

            self.mqtt.publish(f'from/irc/{args[0][1:]}/topic', args[1])

        elif command == 'INVITE':
            # do not enter any channel, only the selected
            for channel in self.channels:
                if self.send(f'JOIN {channel}') == False:
                    self._set_state(self.session_state.DISCONNECTING)

                    break

        else:
            print(f'irc::run: command "{command}" is not known (for {prefix})')

    def irc_command_insertion_point(self, prefix, command, arguments):
        return True

    def handle_irc_command_thread_wrapper(self, prefix, command, arguments):
        try:
            if self.irc_command_insertion_point(prefix, command, arguments):
                self.handle_irc_commands(prefix, command, arguments)

        except Exception as e:
            print(f'irc::handle_irc_command_thread_wrapper: exception "{e}" during execution of IRC command "{command}" at line number: {e.__traceback__.tb_lineno}')

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
                    print(f'irc::run: failed to connect: {e}')
                    
                    self.fd.close()

            elif self.state == self.session_state.CONNECTED_NICK:
                # apparently only error responses are returned, no acks
                if self.send(f'NICK {self.nick}'):
                    self._set_state(self.session_state.CONNECTED_USER)

            elif self.state == self.session_state.CONNECTED_USER:
                if self.send(f'USER {self.nick} 0 * :{self.nick}'):
                    self._set_state(self.session_state.USER_WAIT)

            elif self.state == self.session_state.CONNECTED_JOIN:
                all_ok = True

                for channel in self.channels:
                    if self.send(f'JOIN {channel}') == False:
                        all_ok = False

                        break

                if all_ok:
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
                        print(f'irc::run: cannot decode text from irc-server')

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
                    #print(f'irc::run: state {self.state} timeout ({takes} > {ircbot.state_timeout})')

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

import http.client
import socket
import ssl
import threading
import time

HOST = 'spacenanny.dhcp.nurd.space'
PORT = 9000
VALID_CERT_SUBJECTS = ['DOORCONTROL']

P='/home/ghbot/GHBot/plugins/'
SSL_CA_CERT = P + 'door/ca.crt'
SSL_CERT    = P + 'door/ghbot.crt'
SSL_KEY     = P + 'door/ghbot.key'

door_ts   = None
door_user = None

def init(*args, **kwargs):
    timeout_thread = threading.Thread(target=door_timeout)
    timeout_thread.start()

# returns True if processed (meaning: no other plugin
# should process it)
# else return False
def process(ghbot_instance, nick, parameters):
    global door_ts
    global door_user

    try:
        nick = nick.lower()

        ct = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ct.load_verify_locations(cafile=SSL_CA_CERT)
        ct.load_cert_chain(certfile=SSL_CERT, keyfile=SSL_KEY)
        ct.verify_mode = ssl.CERT_REQUIRED
        ct.check_hostname = False

        conn = http.client.HTTPSConnection(HOST, PORT, context=ct)
        conn.connect()

        subj = conn.sock.getpeercert()['subject'][-1][0][-1]

        if subj not in VALID_CERT_SUBJECTS:
            ghbot_instance.send_ok(parameters[3], f'Incorrect client cert found: {conn.sock.getpeercert()})')
            conn.sock.shutdown(socket.SHUT_RD)
            return False

        if parameters[1] == 'open_door':
            now_ts = time.time()

            age = now_ts - door_ts if door_ts != None else 999999

            print(door_ts, door_user, nick, age, parameters)

            if door_ts == None or door_user == None or door_user == nick:
                ghbot_instance.send_ok(parameters[3], f'open_door please let an other user invoke open_door as well (in 5 seconds)')

                door_ts   = now_ts
                door_user = nick

            elif door_ts != None and door_user != None and door_user != nick and age < 5.0:
                conn.request('GET','/')

                result = conn.getresponse().read().decode()

                ghbot_instance.send_ok(parameters[3], f'open_door result: {result}')

                door_ts   = None
                door_user = None

            elif door_ts != None and age >= 5.0:
                ghbot_instance.send_ok(parameters[3], f'open_door timeout')

                door_ts   = None
                door_user = None

            else:
                ghbot_instance.send_ok(parameters[3], f'open_door failure (state)')

                door_ts   = None
                door_user = None

            conn.close()

            return True

    except Exception as e:
        ghbot_instance.send_ok(parameters[3], f'open_door result: "{e}" at line number: {e.__traceback__.tb_lineno}')

    return False

def get_commandos():
    return [
            ('open_door', ['Open the front door', 'doorcontrol', 0, 'Flok', 'local plugin', 'door']),
            ('lock_door', ['Lock the front door', 'doorcontrol', 0, 'Flok', 'local plugin', 'door']),
            ('unlock_door', ['Unlock the front door', 'doorcontrol', 0, 'Flok', 'local plugin', 'door'])
            ]

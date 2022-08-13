def init(*args, **kwargs):
    print(args, kwargs)

# returns True if processed (meaning: no other plugin
# should process it)
# else return False
def process(ghbot_instance, nick, parameters):
    if parameters[1] == 'open_door':
        print('HIER ************************************', ghbot_instance)
        print(nick, parameters)
        ghbot_instance.send_ok(parameters[3], 'Door is open or maybe closed, no idea')
        return True

    return False

def get_commandos():
    return [
            ('open_door', ['Open the front door', 'bestuur', 0, 'Flok', 'local plugin']),
            ('lock_door', ['Lock the front door', 'bestuur', 0, 'Flok', 'local plugin']),
            ('unlock_door', ['Unlock the front door', 'bestuur', 0, 'Flok', 'local plugin'])
            ]

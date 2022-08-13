def init(*args, **kwargs):
    print(args, kwargs)

# returns True if processed (meaning: no other plugin
# should process it)
# else return False
def process(nick, command_text):
    print(nick, command_text)
    return False

def get_commandos():
    return [
            ('open_door', ['Open the front door', 'bestuur', 0, 'Flok', 'local plugin']),
            ('lock_door', ['Lock the front door', 'bestuur', 0, 'Flok', 'local plugin']),
            ('unlock_door', ['Unlock the front door', 'bestuur', 0, 'Flok', 'local plugin'])
            ]

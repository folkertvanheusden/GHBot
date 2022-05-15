#! /usr/bin/python3

import MySQLdb
import threading
import time


class dbi(threading.Thread):
    def __init__(self, host, user, password, database):
        super().__init__()

        self.host = host
        self.user = user
        self.password = password
        self.database = database

        self.reconnect()

        self.name = 'GHBot MySQL'
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

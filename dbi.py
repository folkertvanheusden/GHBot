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
        self.db = MySQLdb.connect(self.host, self.user, self.password, self.database, charset="utf8mb4", use_unicode=True)

        cursor = self.db.cursor()

        cursor.execute('SET NAMES utf8mb4')
        cursor.execute("SET CHARACTER SET utf8mb4")
        cursor.execute("SET character_set_connection=utf8mb4")

        cursor.close()


    def probe(self):
        try:
            cursor = self.db.cursor()

            cursor.execute('SELECT NOW()')

            cursor.fetchone()

            cursor.close()

        except Exception as e:
            print(f'MySQL indicated error: {e}')

            self.reconnect()

    def run(self):
        while True:
            self.probe()

            time.sleep(29)

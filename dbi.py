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

        while True:
            try:
                self.reconnect()

                self.probe()

                break

            except Exception as e:
                print(f'Cannot connect to MySQL: {e}')

                time.sleep(1)

        self.name = 'GHBot MySQL'
        self.start()

    def reconnect(self):
        try:
            self.db = MySQLdb.connect(self.host, self.user, self.password, self.database, charset="utf8mb4", use_unicode=True)

            cursor = self.db.cursor()

            cursor.execute('SET NAMES utf8mb4')
            cursor.execute("SET CHARACTER SET utf8mb4")
            cursor.execute("SET character_set_connection=utf8mb4")

            cursor.close()

        except Exception as e:
            print(f'dbi::reconnect: exception "{e}" at line number: {e.__traceback__.tb_lineno}')

    def probe(self):
        try:
            cursor = self.db.cursor()

            cursor.execute('SELECT NOW(), VERSION()')

            cursor.fetchone()

            cursor.close()

        except Exception as e:
            print(f'dbi::probe: MySQL indicated error: {e}')

            self.reconnect()

    def run(self):
        while True:
            self.probe()

            time.sleep(29)

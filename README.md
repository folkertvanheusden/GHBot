You may want to add yourself in the sysops acl table.

e.g.:

    INSERT INTO acls VALUES('nickname!username@host', 'sysops');

ghbot.sql contains the database schema.

ghbot.py is the main program.

You may need to install python3-mysqldb, python3-paho-mqtt and python3-nltk.

If you don't run NURDSpace then delete plugins/ghb_door.py :-)


See https://nurdspace.nl/GHBot for more documentation.


(c) 2022-2025 by Folkert van Heusden <folkert@vanheusden.com>

This software is released into the public domain. For Europe
that is the CC0 license if I understood it correctly.

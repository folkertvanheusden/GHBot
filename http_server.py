#! /usr/bin/python3

import http.server
import socketserver
import threading
import time


class http_requesthandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

        page  = '<html>'
        page += '<head><title>GHBot</title></head>'
        page += '<body>'
        page += '<h1>GHbot</h1>'

        page += '<h2>loaded plugins</h2>'
        page += '<table>'
        page += '<tr><th>command</th><th>group</th><th>author</th><th>location</th></tr>'
        page += '<tr><th colspan=4>description</th></tr>'

        for p in self.server.context_data.plugins:
            record = self.server.context_data.plugins[p]

            # [descr, acl_group, time.time(), athr, location]

            page += f'<tr><td>{p}</td><td>{record[1]}</td><td>{record[3]}</td><td>{record[4]}</td></tr>'
            page += f'<tr><td colspan=4>{record[0]}</td></tr>'

        page += '</table>'

        page += '</body>'
        page += '</html>'

        self.wfile.write(bytes(page, 'utf8'))

class http_server(threading.Thread):
    def __init__(self, port, ghbot):
        super().__init__()

        self.ghbot = ghbot
        self.port  = port

        self.name = 'GHBot HTTP'
        self.start()

    def run(self):
        socketserver.TCPServer.allow_reuse_address = True

        while True:
            server = socketserver.TCPServer(('', self.port), http_requesthandler)

            server.context_data = self.ghbot

            server.serve_forever()

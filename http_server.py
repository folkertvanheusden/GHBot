#! /usr/bin/python3

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import pickle
import socketserver
import threading
import time
from urllib.parse import parse_qs


class http_requesthandler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path

        if '?' in p:
            p = p[0:p.find('?')]

        if p == '/index.html' or p == '/':
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

        elif p == '/plugins-loaded.cgi':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            plugins = []

            for p in self.server.context_data.plugins:
                record = self.server.context_data.plugins[p]

                record_out = dict()
                record_out['command']   = p
                record_out['descr']     = record[0]
                record_out['acl_group'] = record[1]
                record_out['latest_ka'] = record[2]
                record_out['author']    = record[3]
                record_out['location']  = record[4]

                plugins.append(record_out)

            self.wfile.write(bytes(json.dumps(plugins), 'utf8'))

        elif p == '/plugins-unresponsive.cgi':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            self.wfile.write(bytes(json.dumps(self.server.plugins_gone), 'utf8'))

        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            self.wfile.write(bytes('nope', 'utf8'))


    def do_POST(self):
        p = self.path

        if p == '/post-message.cgi':
            content_len = int(self.headers['Content-Length'])
            raw_body = self.rfile.read(content_len)
            utf8_body = raw_body.decode('utf8')
            parsed_input = json.loads(utf8_body)

            if 'channel' in parsed_input and 'text' in parsed_input:
                self.server.context_data.send_ok(parsed_input['channel'], parsed_input['text'])

                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()

                self.wfile.write(bytes('ok', 'utf8'))

            else:
                self.send_response(500)
                self.send_header('Content-type', 'text/html')
                self.end_headers()

                self.wfile.write(bytes('Parameter(s) missing', 'utf8'))

        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            self.wfile.write(bytes('nope', 'utf8'))

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

if __name__ == "__main__":
    h = http_server(8123, None)

    time.sleep(1000)

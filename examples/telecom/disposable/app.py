"""Disposable non-telecom readiness endpoint; no credentials or external IO."""
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path=='/healthz' else 404)
        self.end_headers();self.wfile.write(b'harmless research fixture\n')
    def log_message(self,*args):pass
if __name__=='__main__':HTTPServer(('0.0.0.0',8080),Handler).serve_forever()

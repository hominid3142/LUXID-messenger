import http.server
import socketserver

PORT = 5000

Handler = http.server.SimpleHTTPRequestHandler

print(f"Starting Python server on port {PORT}")
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print("serving at port", PORT)
    httpd.serve_forever()

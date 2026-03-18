"""Simple HTTP server for previewing blog HTML with correct image paths."""

import http.server
import mimetypes
import socket
import sys
import urllib.parse
from pathlib import Path

BLOG_DIR = Path(__file__).resolve().parent.parent / "output" / "blogs"

mimetypes.add_type("text/html", ".html")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")


class BlogPreviewHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        decoded = urllib.parse.unquote(self.path.lstrip("/"))
        file_path = BLOG_DIR / decoded if decoded else BLOG_DIR

        if file_path.is_dir():
            self._serve_directory(file_path)
        elif file_path.is_file():
            self._serve_file(file_path)
        else:
            self.send_error(404, f"Not found: {decoded}")
            print(f"  [404] resolved to: {file_path}", flush=True)

    def do_HEAD(self):
        self.do_GET()

    def _serve_file(self, path: Path):
        content_type, _ = mimetypes.guess_type(str(path))
        content_type = content_type or "application/octet-stream"
        try:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_directory(self, path: Path):
        items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        links = []
        for item in items:
            rel = item.relative_to(BLOG_DIR)
            name = item.name + ("/" if item.is_dir() else "")
            encoded = urllib.parse.quote(str(rel).replace("\\", "/"))
            links.append(f'<li><a href="/{encoded}">{name}</a></li>')
        body = f"<html><body><h1>Index of /{path.relative_to(BLOG_DIR)}</h1><ul>{''.join(links)}</ul></body></html>"
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"{args[0]} -> {args[1]}", flush=True)


class DualStackHTTPServer(http.server.HTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9002
    server = DualStackHTTPServer(("::", port), BlogPreviewHandler)
    print(f"Blog preview server running at http://localhost:{port}/", flush=True)
    print(f"Serving from: {BLOG_DIR}", flush=True)
    print(f"Open: http://localhost:{port}/shopify_ready/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()

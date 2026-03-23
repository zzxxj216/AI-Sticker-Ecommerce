"""
简单文件共享服务器
将 install.ps1 通过 HTTP 对外提供下载
"""

import http.server
import socketserver
import os
import socket
import urllib.request

# 配置
PORT = 8899
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_NAME = "install.ps1"
FILE_PATH = os.path.join(SCRIPT_DIR, FILE_NAME)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as resp:
            return resp.read().decode()
    except Exception:
        return None


class InstallScriptHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/install.ps1"):
            try:
                with open(FILE_PATH, "rb") as f:
                    content = f.read()

                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{FILE_NAME}"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

                client_ip = self.client_address[0]
                print(f"[下载] {client_ip} 下载了 {FILE_NAME}")
            except FileNotFoundError:
                self.send_error(404, f"找不到文件: {FILE_PATH}")
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        pass  # 屏蔽默认日志，使用自定义输出


def main():
    if not os.path.exists(FILE_PATH):
        print(f"[错误] 找不到文件: {FILE_PATH}")
        return

    local_ip = get_local_ip()
    public_ip = get_public_ip()

    print("=" * 55)
    print("  install.ps1 文件共享服务器")
    print("=" * 55)
    print(f"  文件路径 : {FILE_PATH}")
    print(f"  监听端口 : {PORT}")
    print()
    print("  访问地址:")
    print(f"    本机        : http://127.0.0.1:{PORT}/install.ps1")
    print(f"    局域网      : http://{local_ip}:{PORT}/install.ps1")
    if public_ip:
        print(f"    公网(需转发) : http://{public_ip}:{PORT}/install.ps1")
    print()
    print("  PowerShell 一键运行命令:")
    print(f"    irm http://{local_ip}:{PORT}/install.ps1 | iex")
    if public_ip:
        print(f"    irm http://{public_ip}:{PORT}/install.ps1 | iex")
    print()
    print("  注意: 公网访问需要在路由器/防火墙开放端口", PORT)
    print("=" * 55)
    print("  按 Ctrl+C 停止服务器")
    print()

    with socketserver.TCPServer(("", PORT), InstallScriptHandler) as httpd:
        httpd.allow_reuse_address = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[停止] 服务器已关闭")


if __name__ == "__main__":
    main()

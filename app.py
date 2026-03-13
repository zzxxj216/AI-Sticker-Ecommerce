"""
启动 Gradio Web UI

快速启动脚本
"""
import socket
from src.ui import launch_app


def find_free_port(start_port=7860, max_attempts=10):
    """查找可用端口"""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return start_port


if __name__ == "__main__":
    # 查找可用端口
    port = find_free_port(7860)

    print("正在启动 AI 贴纸生成器...")
    print(f"使用端口: {port}")
    print("-" * 60)

    launch_app(
        server_name="127.0.0.1",  # 使用本地地址更安全
        server_port=port,  # 使用找到的可用端口
        share=False,  # 设置为 True 可创建公共链接
        debug=False  # 关闭 debug 模式避免 Python 3.13 兼容性问题
    )

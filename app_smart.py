"""
智能启动脚本 - 自动查找可用端口

如果默认端口被占用，会自动尝试其他端口
"""

from src.ui import launch_app
import socket

def find_free_port(start_port=7860, max_attempts=10):
    """查找可用端口
    
    Args:
        start_port: 起始端口
        max_attempts: 最大尝试次数
        
    Returns:
        int: 可用端口号
    """
    for port in range(start_port, start_port + max_attempts):
        try:
            # 尝试绑定端口
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    
    raise RuntimeError(f"无法在 {start_port}-{start_port + max_attempts} 范围内找到可用端口")

if __name__ == "__main__":
    # 查找可用端口
    try:
        port = find_free_port(start_port=7860)
        print(f"使用端口: {port}")
        print(f"访问地址: http://localhost:{port}")
        print("-" * 60)
        
        # 启动应用
        launch_app(
            server_name="0.0.0.0",
            server_port=port,
            share=False
        )
    except Exception as e:
        print(f"启动失败: {e}")
        print("\n提示：")
        print("1. 检查是否有其他 Gradio 应用正在运行")
        print("2. 尝试手动指定端口：")
        print("   python -c \"from src.ui import launch_app; launch_app(server_port=7862)\"")

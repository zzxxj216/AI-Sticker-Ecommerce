"""
端口检查工具 - 检查常用端口是否可用
"""

import socket

def check_port(port):
    """检查端口是否可用
    
    Args:
        port: 端口号
        
    Returns:
        bool: True 表示可用，False 表示被占用
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', port))
            return True
    except OSError:
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Port Availability Check")
    print("=" * 60)
    
    ports_to_check = [7860, 7861, 7862, 7863, 7864, 7865]
    
    available_ports = []
    occupied_ports = []
    
    for port in ports_to_check:
        if check_port(port):
            status = "[AVAILABLE]"
            available_ports.append(port)
        else:
            status = "[OCCUPIED]"
            occupied_ports.append(port)
        
        print(f"Port {port}: {status}")
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    if available_ports:
        print(f"\nAvailable ports: {', '.join(map(str, available_ports))}")
        print(f"\nRecommended: Use port {available_ports[0]}")
        print(f"\nTo start the app:")
        print(f"  python app_smart.py")
        print(f"  or")
        print(f'  python -c "from src.ui import launch_app; launch_app(server_port={available_ports[0]})"')
    else:
        print("\nNo available ports found in the checked range.")
        print("Try closing other applications or use a different port range.")
    
    if occupied_ports:
        print(f"\nOccupied ports: {', '.join(map(str, occupied_ports))}")

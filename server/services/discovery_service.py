"""
UDP 广播发现服务
服务端监听 UDP 广播，客户端发送广播后自动回复服务地址
"""
import socket
import threading
import json
import logging

logger = logging.getLogger(__name__)

DISCOVERY_PORT = 9999  # UDP 发现端口
DISCOVERY_MESSAGE = "LEARNING_ASSISTANT_DISCOVER"  # 发现协议


class DiscoveryService:
    """UDP 广播发现服务"""
    
    def __init__(self, server_host: str = "0.0.0.0", api_port: int = 8000):
        """
        Args:
            server_host: 服务端实际 IP（自动检测）
            api_port: API 服务端口
        """
        self.server_host = server_host or self._get_local_ip()
        self.api_port = api_port
        self.running = False
        self.thread: threading.Thread | None = None
    
    @staticmethod
    def _get_local_ip() -> str:
        """获取本机局域网IP（优先读取项目根目录 server_ip.txt，用户可手动指定）"""
        try:
            from pathlib import Path
            ip_file = Path(__file__).resolve().parent.parent.parent / "server_ip.txt"
            if ip_file.exists():
                content = ip_file.read_text(encoding="utf-8").strip()
                ip = content.split(":")[0].strip()
                if ip:
                    return ip
        except Exception:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    
    def start(self):
        """启动发现服务（后台线程）"""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f"发现服务已启动，广播地址: {self.server_host}:{DISCOVERY_PORT}")
    
    def stop(self):
        """停止发现服务"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def _run(self):
        """主循环：监听 UDP 广播并回复"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1)
        
        try:
            sock.bind(("0.0.0.0", DISCOVERY_PORT))
            logger.info(f"UDP 发现服务绑定 0.0.0.0:{DISCOVERY_PORT}")
            
            while self.running:
                try:
                    data, addr = sock.recvfrom(1024)
                    message = data.decode("utf-8").strip()
                    
                    if message == DISCOVERY_MESSAGE:
                        # 回复服务地址
                        response = json.dumps({
                            "service": "LearningAssistant",
                            "host": self.server_host,
                            "api_port": self.api_port,
                        })
                        sock.sendto(response.encode("utf-8"), addr)
                        logger.info(f"发现请求来自 {addr[0]}，已回复服务地址: {self.server_host}:{self.api_port}")
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.error(f"发现服务错误: {e}")
        finally:
            sock.close()
        logger.info("发现服务已停止")
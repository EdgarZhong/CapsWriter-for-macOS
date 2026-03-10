import os
import sys
import time
import subprocess
import psutil
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("monitor.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("CapsMonitor")

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(BASE_DIR, "core_server.py")
CLIENT_SCRIPT = os.path.join(BASE_DIR, "core_client.py")

def is_process_running(script_name):
    """检查指定脚本是否正在运行"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # 兼容 python.exe 和 pythonw.exe
            if proc.info['name'] in ['python.exe', 'pythonw.exe']:
                cmdline = proc.info['cmdline']
                # cmdline 是一个列表，通常第二个参数是脚本路径
                if cmdline:
                    for arg in cmdline:
                        if script_name in arg:
                            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def start_process(script_path):
    """启动进程（保持窗口可见）"""
    logger.info(f"正在启动: {script_path}")
    
    # 强制使用 python.exe (有窗口)，而不是 pythonw.exe
    python_exec = sys.executable
    if python_exec.endswith("pythonw.exe"):
        python_exec = python_exec.replace("pythonw.exe", "python.exe")
            
    cmd = [python_exec, script_path]
    
    # Windows 标志：使用 CREATE_NEW_CONSOLE 确保每个服务都有独立窗口
    creationflags = 0
    if sys.platform == 'win32':
        creationflags = subprocess.CREATE_NEW_CONSOLE
        
    try:
        subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            creationflags=creationflags,
            close_fds=True
        )
        logger.info(f"启动成功: {script_path}")
    except Exception as e:
        logger.error(f"启动失败 {script_path}: {e}")

def monitor_loop():
    """主监控循环"""
    logger.info("CapsWriter 监控服务已启动")
    
    # 启动缓冲时间 (秒) - 避免因模型加载慢而反复重启
    GRACE_PERIOD = 30
    last_start_time = {}
    
    while True:
        now = time.time()
        
        # 检查 Server
        if not is_process_running("core_server.py"):
            # 只有当上次启动超过缓冲时间，或者从未启动过，才尝试启动
            if now - last_start_time.get("server", 0) > GRACE_PERIOD:
                logger.warning("核心服务 (core_server.py) 未运行，正在重启...")
                start_process(SERVER_SCRIPT)
                last_start_time["server"] = now
            
        # 检查 Client
        if not is_process_running("core_client.py"):
            if now - last_start_time.get("client", 0) > GRACE_PERIOD:
                logger.warning("客户端 (core_client.py) 未运行，正在重启...")
                start_process(CLIENT_SCRIPT)
                last_start_time["client"] = now
            
        # 每 5 秒检查一次
        time.sleep(5)

if __name__ == "__main__":
    # 确保单例运行
    current_pid = os.getpid()
    monitor_running = False
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] != current_pid and proc.info['name'] in ['python.exe', 'pythonw.exe']:
                cmdline = proc.info['cmdline']
                if cmdline:
                    for arg in cmdline:
                        if "caps_monitor.py" in arg:
                            monitor_running = True
                            break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
            
    if monitor_running:
        print("监控程序已在运行中。")
        sys.exit(0)
        
    try:
        monitor_loop()
    except KeyboardInterrupt:
        logger.info("监控服务已停止")

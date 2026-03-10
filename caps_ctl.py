import argparse
import sys
import os
import subprocess
import psutil
import time
import shutil
import signal

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_SERVER_PATH = os.path.join(BASE_DIR, "config_server.py")
MONITOR_SCRIPT = os.path.join(BASE_DIR, "caps_monitor.py")

def get_running_pids(script_name):
    """获取指定脚本的运行 PID 列表"""
    pids = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] in ['python.exe', 'pythonw.exe']:
                cmdline = proc.info['cmdline']
                if cmdline:
                    for arg in cmdline:
                        if script_name in arg:
                            pids.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return pids

def stop_process(script_name):
    """停止指定脚本的所有进程"""
    pids = get_running_pids(script_name)
    if not pids:
        print(f"未发现正在运行的 {script_name}")
        return
    
    print(f"正在停止 {script_name} (PID: {pids})...")
    
    # 策略：直接使用 taskkill 强制终止进程
    # 不再尝试发送信号，避免跨进程组权限问题
    for pid in pids:
        try:
            if sys.platform == 'win32':
                # /F: 强制终止
                # /T: 终止进程树（包括子进程）
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False
                )
            else:
                p = psutil.Process(pid)
                p.kill()
        except Exception:
            pass

    # 等待进程彻底消失
    timeout = 3
    start_time = time.time()
    while time.time() - start_time < timeout:
        remaining = get_running_pids(script_name)
        if not remaining:
            break
        time.sleep(0.5)
        
    if get_running_pids(script_name):
        print(f"警告: {script_name} 依然存在，请手动检查。")
    else:
        print(f"{script_name} 已停止。")

def start_monitor():
    """启动监控进程"""
    if get_running_pids("caps_monitor.py"):
        print("监控服务已在运行。")
        return

    print("正在启动监控服务...")
    # 使用 pythonw.exe 启动无窗口进程
    python_exec = sys.executable
    if python_exec.endswith("python.exe"):
        pythonw = python_exec.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw):
            python_exec = pythonw
            
    cmd = [python_exec, MONITOR_SCRIPT]
    
    creationflags = 0
    if sys.platform == 'win32':
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True
    )
    print("监控服务启动成功。")

def switch_mode(mode):
    """切换显卡模式"""
    if mode not in ['performance', 'saving']:
        print("错误：模式必须是 'performance' 或 'saving'")
        return

    print(f"正在切换到 {mode} 模式...")
    
    # 读取 config_server.py
    try:
        with open(CONFIG_SERVER_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        new_lines = []
        found = False
        for line in lines:
            if "gpu_selection_mode =" in line:
                # 保持原有缩进
                indent = line[:line.find("gpu_selection_mode")]
                new_lines.append(f'{indent}gpu_selection_mode = "{mode}"\n')
                found = True
            else:
                new_lines.append(line)
                
        if found:
            with open(CONFIG_SERVER_PATH, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
            print("配置已更新。正在重启服务以生效...")
            
            # 停止所有相关进程
            # 必须先停止监控，防止它自动拉起 Server
            stop_process("caps_monitor.py")
            # 确保 Server 被彻底杀死，释放显存
            stop_process("core_server.py")
            
            # 等待显存释放
            time.sleep(2)
            
            # 重启监控（监控会自动拉起 server 和 client）
            start_monitor()
            print("切换完成！")
        else:
            print("错误：在 config_server.py 中未找到 'gpu_selection_mode' 配置项。")
    except Exception as e:
        print(f"切换模式失败: {e}")

def main():
    parser = argparse.ArgumentParser(description="CapsWriter-Offline 控制工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # start 命令
    subparsers.add_parser("start", help="启动服务 (通过监控进程)")

    # stop 命令
    subparsers.add_parser("stop", help="停止所有服务")

    # restart 命令
    subparsers.add_parser("restart", help="重启服务")

    # status 命令
    subparsers.add_parser("status", help="查看服务状态")

    # mode 命令
    mode_parser = subparsers.add_parser("mode", help="切换显卡模式")
    mode_parser.add_argument("type", choices=['performance', 'saving'], help="模式类型: performance (独显) / saving (集显)")

    args = parser.parse_args()

    if args.command == "start":
        start_monitor()
        
    elif args.command == "stop":
        stop_process("caps_monitor.py")
        stop_process("core_server.py")
        stop_process("core_client.py")
        print("所有服务已停止。")
        
    elif args.command == "restart":
        stop_process("caps_monitor.py")
        stop_process("core_server.py")
        stop_process("core_client.py")
        start_monitor()
        print("服务已重启。")
        
    elif args.command == "status":
        monitor_pids = get_running_pids("caps_monitor.py")
        server_pids = get_running_pids("core_server.py")
        client_pids = get_running_pids("core_client.py")
        
        print(f"Monitor PID: {monitor_pids if monitor_pids else '未运行'}")
        print(f"Server PID:  {server_pids if server_pids else '未运行'}")
        print(f"Client PID:  {client_pids if client_pids else '未运行'}")
        
    elif args.command == "mode":
        switch_mode(args.type)
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

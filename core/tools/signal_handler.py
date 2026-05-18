import signal
import sys
import time
import asyncio


class SignalHandler:
    def __init__(self, callback):
        self.last_time = time.time()
        self.callback = callback

    def __call__(self, signum, frame):
        now = time.time()
        if now - self.last_time > 1.0:
            self.last_time = now
            print(f"\n收到 {signal.Signals(signum).name}，1秒内再次按下将会退出...")
        else:
            print(f"\n收到 {signal.Signals(signum).name}，确认退出...\n")
            self.last_time = 0
            self.callback()
            
def register_signal(callback):
    # SIGINT：双击确认退出（交互场景）
    signal.signal(signal.SIGINT, SignalHandler(callback))
    # SIGTERM：立即执行 cleanup（capswriterd / launchd 停止场景）
    signal.signal(signal.SIGTERM, lambda signum, frame: (callback(), sys.exit(0)))
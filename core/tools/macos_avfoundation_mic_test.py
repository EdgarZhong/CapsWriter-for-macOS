#!/usr/bin/env python3
# coding: utf-8
"""
macOS 麦克风指示灯 AVFoundation 路径测试

目的：通过 AVFoundation（macOS 原生 AVCaptureSession）打开麦克风，
验证系统菜单栏橙色圆点是否出现。

与 sounddevice（PortAudio/CoreAudio HAL）的区别：
- PortAudio 走 CoreAudio HAL 层，部分 macOS 版本下不触发隐私指示灯
- AVFoundation（AVCaptureSession）是苹果官方推荐的录音路径，
  与相机应用、FaceTime 等 App 使用同一套指示灯机制

运行方式：
    python -m core.tools.macos_avfoundation_mic_test

前提：需要安装 Xcode Command Line Tools（有 swift 命令即可）
    xcode-select --install
"""

import sys
import os
import platform
import subprocess
import tempfile
import time

if platform.system() != 'Darwin':
    print("此脚本仅用于 macOS，退出。")
    sys.exit(1)

# 检查 swift 是否可用
result = subprocess.run(['which', 'swift'], capture_output=True)
if result.returncode != 0:
    print("未找到 swift 命令，请安装 Xcode Command Line Tools：")
    print("  xcode-select --install")
    sys.exit(1)

print(f"[avf-test] swift 路径: {result.stdout.decode().strip()}")

# 用 AVFoundation 录音的 Swift 小程序
SWIFT_CODE = '''
import AVFoundation
import Foundation

// 请求麦克风权限，然后用 AVCaptureSession 录制
class MicRecorder: NSObject {
    let session = AVCaptureSession()
    var isRunning = false

    func start() -> Bool {
        guard let device = AVCaptureDevice.default(for: .audio) else {
            print("[swift] 找不到音频设备")
            return false
        }
        do {
            let input = try AVCaptureDeviceInput(device: device)
            if session.canAddInput(input) {
                session.addInput(input)
            }
            let output = AVCaptureAudioDataOutput()
            if session.canAddOutput(output) {
                session.addOutput(output)
            }
            session.startRunning()
            isRunning = true
            print("[swift] AVCaptureSession 已启动，橙色圆点应出现在菜单栏右侧")
            return true
        } catch {
            print("[swift] 启动失败: \\(error)")
            return false
        }
    }

    func stop() {
        session.stopRunning()
        print("[swift] AVCaptureSession 已停止，橙色圆点应消失")
    }
}

let recorder = MicRecorder()
guard recorder.start() else {
    exit(1)
}

// 录制 8 秒
for i in 1...8 {
    Thread.sleep(forTimeInterval: 1.0)
    print("[swift] \\(i)s ...")
}

recorder.stop()
'''

def main():
    print()
    print("=" * 55)
    print("  AVFoundation 麦克风指示灯测试")
    print("=" * 55)
    print()
    print(">>> 请观察 macOS 菜单栏右侧（时钟左边）控制中心区域")
    print(">>> 橙色圆点应在 'AVCaptureSession 已启动' 后立即出现")
    print(">>> 脚本结束后圆点应消失")
    print()

    # 写入临时 Swift 文件并编译运行
    with tempfile.NamedTemporaryFile(suffix='.swift', mode='w', delete=False) as f:
        f.write(SWIFT_CODE)
        swift_path = f.name

    try:
        print(f"[avf-test] 正在启动 Swift 进程（首次可能稍慢）...")
        proc = subprocess.Popen(
            ['swift', swift_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # 实时打印 Swift 输出
        import threading

        def stream_output(pipe, label):
            for line in pipe:
                line = line.rstrip()
                if line:
                    print(f"  {line}")

        t_out = threading.Thread(target=stream_output, args=(proc.stdout, 'stdout'), daemon=True)
        t_err = threading.Thread(target=stream_output, args=(proc.stderr, 'stderr'), daemon=True)
        t_out.start()
        t_err.start()

        proc.wait()
        t_out.join(timeout=2)
        t_err.join(timeout=2)

        print()
        if proc.returncode == 0:
            print("[avf-test] Swift 进程正常退出。")
        else:
            print(f"[avf-test] Swift 进程退出码: {proc.returncode}")

    except KeyboardInterrupt:
        print("\n[avf-test] 用户中断，正在终止...")
        proc.terminate()
    finally:
        os.unlink(swift_path)

    print()
    print("结论：")
    print("  - 若橙色圆点出现 → AVFoundation 路径有效，")
    print("    需要把 CapsWriter 录音从 sounddevice 改为 AVFoundation 后端")
    print("  - 若橙色圆点仍未出现 → 系统级权限或 macOS 版本行为差异，")
    print("    需要进一步排查 TCC 权限设置")


if __name__ == '__main__':
    main()

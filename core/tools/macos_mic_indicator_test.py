#!/usr/bin/env python3
# coding: utf-8
"""
macOS 麦克风指示灯独立测试脚本

目的：验证在不依赖项目其他代码的前提下，
sounddevice 打开音频流后系统菜单栏/控制中心的麦克风
橙色圆点指示是否会出现。

运行方式（在项目根目录下）：
    python -m core.tools.macos_mic_indicator_test

或直接运行（需要已激活 .venv 或全局安装了 sounddevice）：
    python core/tools/macos_mic_indicator_test.py

预期现象：
    - 脚本运行后，macOS 右上角控制中心区域（紧靠时间左侧）
      应出现橙色圆点，表示麦克风正在被占用。
    - 注意：指示灯在"控制中心"侧（菜单栏右侧），不是左侧 App 区。
    - 5 秒后脚本退出，橙色圆点消失。
"""

import sys
import time
import signal
import platform

if platform.system() != 'Darwin':
    print("此脚本仅用于 macOS，退出。")
    sys.exit(1)

try:
    import sounddevice as sd
    import numpy as np
except ImportError:
    print("缺少依赖，请先激活 .venv 后运行：")
    print("  source .venv/bin/activate")
    print("  python -m core.tools.macos_mic_indicator_test")
    sys.exit(1)


SAMPLE_RATE = 48000
BLOCK_DURATION = 0.05   # 50ms
DURATION = 10.0          # 总录制时长（秒）


def main():
    # 查询默认输入设备
    try:
        device_info = sd.query_devices(kind='input')
        device_name = device_info.get('name', '未知设备')
        channels = min(2, device_info.get('max_input_channels', 1))
    except Exception as e:
        print(f"查询音频设备失败: {e}")
        sys.exit(1)

    print(f"[mic-test] 设备: {device_name}，声道数: {channels}")
    print(f"[mic-test] 将打开麦克风 {DURATION:.0f} 秒...")
    print()
    print(">>> 请观察 macOS 右上角控制中心区域（紧靠时钟左侧）")
    print(">>> 应出现橙色圆点（麦克风占用指示）")
    print(">>> 按 Ctrl+C 可提前退出")
    print()

    frame_count = [0]
    peak_values = []

    def callback(indata: np.ndarray, frames: int, time_info, status):
        """持续读取并打印简单能量指标"""
        frame_count[0] += 1
        peak = float(np.max(np.abs(indata)))
        peak_values.append(peak)

        # 每秒打印一次（20 个 50ms 块 = 1s）
        if frame_count[0] % 20 == 0:
            recent_peak = max(peak_values[-20:]) if len(peak_values) >= 20 else peak
            bar_len = int(recent_peak * 40)
            bar = "█" * bar_len + "░" * (40 - bar_len)
            elapsed = frame_count[0] * BLOCK_DURATION
            print(f"  [{elapsed:5.1f}s] 音量: |{bar}| {recent_peak:.4f}")

    # 打开流
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=int(BLOCK_DURATION * SAMPLE_RATE),
        device=None,
        dtype='float32',
        channels=channels,
        callback=callback,
    )

    # 注册 Ctrl+C 处理，确保流能被正常关闭
    def _sigint(sig, frame):
        print("\n[mic-test] 收到中断，正在关闭流...")
        stream.close()
        print("[mic-test] 已关闭，橙色指示应已消失。")
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)

    with stream:
        print("[mic-test] 流已打开，开始读取麦克风...")
        time.sleep(DURATION)

    print()
    print(f"[mic-test] 完成，共读取 {frame_count[0]} 帧")
    print("[mic-test] 流已关闭，橙色指示应已消失。")


if __name__ == '__main__':
    main()

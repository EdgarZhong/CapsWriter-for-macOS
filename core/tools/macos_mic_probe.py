# coding: utf-8
"""
macOS 麦克风输入能量探针

这个脚本故意绕开 `Caps Lock` 和现有录音状态机，只验证一件事：
当前默认输入设备是否真的在向应用提供可用的麦克风波形。

输出指标说明：
1. rms: 均方根能量，最适合观察“静音”和“说话”之间的幅值差异。
2. peak: 当前采样块的最大绝对值，适合快速观察是否有瞬时输入。
3. mean_abs: 平均绝对值，和 rms 一起看能更直观地分辨近零静音。
4. zero_ratio: 采样块中精确等于 0 的比例；如果长期接近 1.0，通常说明拿到的是全零帧。

典型用法：
    .venv/bin/python -m core.tools.macos_mic_probe --duration 12
"""

from __future__ import annotations

import argparse
import platform
import queue
import sys
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


@dataclass(slots=True)
class ProbeConfig:
    """探针运行参数。"""

    duration: float
    sample_rate: int
    block_duration: float
    summary_interval: float


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description='macOS 麦克风输入能量探针')
    parser.add_argument('--duration', type=float, default=12.0, help='监听总时长（秒）')
    parser.add_argument('--sample-rate', type=int, default=48000, help='采样率')
    parser.add_argument('--block-duration', type=float, default=0.05, help='单个音频块时长（秒）')
    parser.add_argument(
        '--summary-interval',
        type=float,
        default=0.5,
        help='汇总打印间隔（秒），避免每个回调都刷屏',
    )
    return parser


def _parse_args(argv: list[str]) -> ProbeConfig:
    """把命令行参数转换成强类型配置。"""
    args = _build_parser().parse_args(argv)
    return ProbeConfig(
        duration=args.duration,
        sample_rate=args.sample_rate,
        block_duration=args.block_duration,
        summary_interval=args.summary_interval,
    )


def _describe_input_device() -> tuple[str, int]:
    """读取默认输入设备信息。"""
    device = sd.query_devices(kind='input')
    device_name = str(device.get('name', '未知设备'))
    max_input_channels = int(device.get('max_input_channels', 1))
    return device_name, max_input_channels


def main(argv: list[str] | None = None) -> int:
    """脚本入口。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    config = _parse_args(argv)

    if platform.system() != 'Darwin':
        print('该探针当前只面向 macOS / Darwin 调试。')
        return 2

    try:
        device_name, max_input_channels = _describe_input_device()
    except Exception as exc:
        print(f'读取默认输入设备失败: {exc}')
        return 1

    channels = min(2, max_input_channels) if max_input_channels > 0 else 1
    blocksize = int(config.sample_rate * config.block_duration)
    metric_queue: queue.Queue[tuple[float, float, float, float, int]] = queue.Queue()

    def audio_callback(indata, frames, time_info, status) -> None:
        """
        声卡回调。

        这里只做极轻量的数值计算和入队，避免回调本身阻塞导致 PortAudio 丢帧。
        """
        audio_data = np.asarray(indata, dtype=np.float32)
        mean_abs = float(np.mean(np.abs(audio_data)))
        rms = float(np.sqrt(np.mean(np.square(audio_data))))
        peak = float(np.max(np.abs(audio_data)))
        zero_ratio = float(np.mean(audio_data == 0.0))
        metric_queue.put((time.time(), rms, peak, mean_abs, zero_ratio, frames))

    print(
        f'READY: device={device_name} channels={channels} '
        f'sample_rate={config.sample_rate} blocksize={blocksize} duration={config.duration:.1f}s',
        flush=True,
    )
    print('请先静默 2-3 秒，再连续说话 2-3 秒，最后再静默 2-3 秒。', flush=True)

    try:
        stream = sd.InputStream(
            samplerate=config.sample_rate,
            blocksize=blocksize,
            channels=channels,
            dtype='float32',
            callback=audio_callback,
        )
    except Exception as exc:
        print(f'创建输入流失败: {exc}')
        return 1

    summary_samples: list[tuple[float, float, float, float, int]] = []
    last_summary_at = time.time()
    end_time = last_summary_at + config.duration

    try:
        with stream:
            while time.time() < end_time:
                timeout = max(0.05, min(config.summary_interval, end_time - time.time()))
                try:
                    sample = metric_queue.get(timeout=timeout)
                    summary_samples.append(sample)
                except queue.Empty:
                    pass

                now = time.time()
                if now - last_summary_at < config.summary_interval and now < end_time:
                    continue

                if summary_samples:
                    rms_values = [item[1] for item in summary_samples]
                    peak_values = [item[2] for item in summary_samples]
                    mean_abs_values = [item[3] for item in summary_samples]
                    zero_ratios = [item[4] for item in summary_samples]
                    frame_counts = [item[5] for item in summary_samples]

                    print(
                        f'{now: .3f} '
                        f'rms_avg={np.mean(rms_values):.6f} '
                        f'rms_max={np.max(rms_values):.6f} '
                        f'peak_max={np.max(peak_values):.6f} '
                        f'mean_abs_avg={np.mean(mean_abs_values):.6f} '
                        f'zero_ratio_avg={np.mean(zero_ratios):.4f} '
                        f'frames={sum(frame_counts)}',
                        flush=True,
                    )
                    summary_samples.clear()

                last_summary_at = now
    except KeyboardInterrupt:
        print('已手动中断。', flush=True)
        return 130

    print('DONE', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

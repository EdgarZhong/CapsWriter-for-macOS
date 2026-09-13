# coding: utf-8
"""
批量转写脚本：把 diagnogs/assets 下的录音逐条发给正在运行的服务端推理，
结果以 Markdown 形式写入 diagnogs/transcripts/<同名>.md。

设计约束（取证要求）：
1. 发送链路与真实客户端"文件转录"功能一模一样：
   - 连接参数逐字复用 core/client/connection/websocket_manager.py 的 connect()
     (subprotocols=["binary"], max_size=None, max_queue=None, websockets>=14 时 proxy=None)
   - 音频封装逐字复用 core/client/transcribe/file_transcriber.py 的 send():
       * MediaTool.build_ffmpeg_cmd() 提取 f32le / 单声道 / 16kHz PCM
       * 按 chunk_size = 16000 * 4 * 60 字节分块
       * AudioMessage(task_id=uuid1, source='file', base64, is_final=False,
         seg_duration=Config.file_seg_duration, seg_overlap=Config.file_seg_overlap,
         context=Config.context, language=Config.language)
       * 数据发完后补发 data='' / is_final=True 的结束消息
   - 因此这里直接 import 客户端现成组件（WebSocketManager / AudioMessage / MediaTool /
     ClientConfig），而不是手写协议，保证零偏差。
2. 只绕过客户端的结果侧后处理（_apply_hotwords 热词替换、ResultHandler 落盘），
   落盘内容为服务端返回的原始 text / text_accu。

用法（在项目根目录，用项目 .venv 运行）:
    .venv/bin/python diagnogs/serve_transcribe.py            # 全量（自动跳过已完成）
    .venv/bin/python diagnogs/serve_transcribe.py --limit 5  # 只跑前 5 条做验证
    .venv/bin/python diagnogs/serve_transcribe.py --start 100 # 从第 100 条开始
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import uuid
from pathlib import Path

# 脚本位于 diagnogs/ 下，先补上项目根目录以便 import 客户端模块
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_client import ClientConfig as Config          # noqa: E402
from core.protocol import AudioMessage, RecognitionMessage  # noqa: E402
from core.client.connection import WebSocketManager, CommunicationError  # noqa: E402
from core.client.transcribe.media_tool import MediaTool    # noqa: E402

ASSETS_DIR = ROOT / 'diagnogs' / 'assets'
TRANSCRIPTS_DIR = ROOT / 'diagnogs' / 'transcripts'
FAILURES_LOG = ROOT / 'diagnogs' / 'serve_transcribe_failures.jsonl'

# 与真实客户端 file_transcriber.py 完全一致的分块大小（1 分钟 16kHz float32 音频）
CHUNK_SIZE = 16000 * 4 * 60

# 单文件最大重试次数（断线重连后重试）
MAX_RETRIES = 3


class _FakeState:
    """
    WebSocketManager 只用到 state.is_connected / state.websocket 两个成员，
    is_connected 的实现逐字对齐 core/client/state.py 的 ClientState.is_connected
    property（由 websocket 对象派生，而非独立布尔值）。
    """

    def __init__(self):
        self.websocket = None

    @property
    def is_connected(self) -> bool:
        """检查 WebSocket 是否已连接（对齐 ClientState.is_connected）"""
        if self.websocket is None:
            return False
        try:
            return not self.websocket.closed
        except AttributeError:
            return self.websocket is not None


class _FakeApp:
    """WebSocketManager 只用到 app.state 与 app.loop（后者仅 close_sync 使用）。"""

    def __init__(self):
        self.state = _FakeState()
        self.loop = None


class ServeTranscriber:
    """批量转写器：复用客户端 WebSocketManager 与协议类，按文件逐条推理。"""

    def __init__(self):
        self.ws = WebSocketManager(_FakeApp())

    async def ensure_connected(self) -> bool:
        """连接管理语义与 FileTranscriber.check() 一致：未连接才建连。"""
        return await self.ws.connect()

    async def transcribe_one(self, file: Path) -> tuple[RecognitionMessage, float, float]:
        """
        转录单个音频文件，返回 (最终 RecognitionMessage, 推理耗时秒, 音频时长秒)。

        发送流程逐字对齐 FileTranscriber.send()，接收流程对齐 receive()，
        仅去掉热词替换与 ResultHandler。
        """
        task_id = str(uuid.uuid1())
        audio_duration = await MediaTool.get_audio_duration(file)

        time_begin = time.time()
        # 启动 FFmpeg 提取 PCM（命令与客户端完全一致）
        ffmpeg_cmd = MediaTool.build_ffmpeg_cmd(file)
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        try:
            # 分块读取 PCM 并封装发送，字段取值与 FileTranscriber.send() 一致
            while True:
                data = await process.stdout.read(CHUNK_SIZE)
                if not data:
                    break

                message = AudioMessage(
                    task_id=task_id,
                    source='file',
                    data=base64.b64encode(data).decode('utf-8'),
                    is_final=False,
                    time_start=time_begin,
                    seg_duration=Config.file_seg_duration,
                    seg_overlap=Config.file_seg_overlap,
                    context=Config.context,
                    language=Config.language,
                )
                if not await self.ws.send(message):
                    raise ConnectionError("消息发送失败，连接可能已断开")

            # 发送结束标志
            final_message = AudioMessage(
                task_id=task_id,
                source='file',
                data='',
                is_final=True,
                time_start=time_begin,
                seg_duration=Config.file_seg_duration,
                seg_overlap=Config.file_seg_overlap,
                context=Config.context,
                language=Config.language,
            )
            if not await self.ws.send(final_message):
                raise ConnectionError("结束标志发送失败")
            await process.wait()

            # 循环接收进度消息，直到 is_final
            final: RecognitionMessage | None = None
            while True:
                msg = await self.ws.receive()
                if msg is None:
                    raise CommunicationError("接收消息返回空，连接可能已断开")
                if msg.is_final:
                    final = msg
                    break

            elapsed = time.time() - time_begin
            return final, elapsed, audio_duration

        except (CommunicationError, ConnectionError):
            # 断线时杀掉 ffmpeg 子进程，交由上层重连重试
            if process.returncode is None:
                process.terminate()
            raise

    async def run(self, files: list[Path]) -> dict:
        """批量转写：已有同名 md 的文件自动跳过，失败写入 failures 日志。"""
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

        stats = {'done': 0, 'skip': 0, 'fail': 0, 'empty': 0}
        fail_records: list[dict] = []

        for idx, file in enumerate(files, 1):
            md_path = TRANSCRIPTS_DIR / (file.stem + '.md')
            if md_path.exists():
                stats['skip'] += 1
                continue

            ok = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if not await self.ensure_connected():
                        print(f'[{idx}/{len(files)}] 无法连接服务端，稍后重试: {file.name}')
                        await asyncio.sleep(2 * attempt)
                        continue

                    msg, elapsed, duration = await self.transcribe_one(file)
                    self._save_md(file, msg, elapsed, duration)
                    if not (msg.text or msg.text_accu):
                        stats['empty'] += 1
                    stats['done'] += 1
                    ok = True
                    print(
                        f'[{idx}/{len(files)}] {file.name} '
                        f'({duration:.1f}s -> {elapsed:.1f}s) '
                        f'{(msg.text_accu or msg.text)[:40]!r}',
                        flush=True,
                    )
                    break
                except Exception as e:  # noqa: BLE001 — 单文件失败不应中断批量任务
                    print(f'[{idx}/{len(files)}] 第 {attempt} 次尝试失败: {file.name}: {e}',
                          flush=True)
                    # 连接可能已坏，强制重建
                    try:
                        await self.ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(2 * attempt)

            if not ok:
                stats['fail'] += 1
                fail_records.append({'file': str(file), 'error': 'max_retries_exceeded'})
                with open(FAILURES_LOG, 'a', encoding='utf-8') as fp:
                    fp.write(json.dumps({'file': str(file), 'ts': time.time()},
                                        ensure_ascii=False) + '\n')

        return stats

    @staticmethod
    def _save_md(file: Path, msg: RecognitionMessage, elapsed: float, duration: float) -> None:
        """把服务端原始结果写成 Markdown（不做任何客户端后处理）。"""
        md_path = TRANSCRIPTS_DIR / (file.stem + '.md')
        text_main = msg.text or ''
        text_accu = msg.text_accu or ''
        lines = [
            f'# {file.stem}',
            '',
            f'- 音频: assets/{file.name}',
            f'- 音频时长: {duration:.2f} s' if duration > 0 else '- 音频时长: 未知',
            f'- 推理耗时: {elapsed:.2f} s',
            f'- 转录耗时: {msg.time_complete - msg.time_start:.2f} s',
            '',
            '## 转写文本 (text)',
            '',
            text_main if text_main else '（空）',
            '',
            '## 精确拼接 (text_accu)',
            '',
            text_accu if text_accu else '（空）',
            '',
        ]
        md_path.write_text('\n'.join(lines), encoding='utf-8')


def collect_files() -> list[Path]:
    """按文件名（即创建时间戳）排序收集全部音频。"""
    exts = {'.mp3', '.wav'}
    return sorted(
        (p for p in ASSETS_DIR.iterdir() if p.suffix.lower() in exts and p.is_file()),
        key=lambda p: p.name,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description='批量把 assets 录音发给服务端转写')
    parser.add_argument('--limit', type=int, default=0, help='最多处理 N 条（0 表示不限制）')
    parser.add_argument('--start', type=int, default=0, help='从第 N 条（0 基）开始')
    args = parser.parse_args()

    files = collect_files()
    if args.start:
        files = files[args.start:]
    if args.limit:
        files = files[:args.limit]

    print(f'待处理 {len(files)} 条，输出目录 {TRANSCRIPTS_DIR}')
    transcriber = ServeTranscriber()
    stats = asyncio.run(transcriber.run(files))
    print(f"完成 done={stats['done']} 跳过={stats['skip']} "
          f"空结果={stats['empty']} 失败={stats['fail']}")


if __name__ == '__main__':
    main()

# coding: utf-8
"""
识别结果处理模块

提供 ResultProcessor 类用于处理服务端返回的识别结果。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from config_client import ClientConfig as Config
from core.client.state import console
from core.protocol import RecognitionMessage

from core.client.output.text_output import TextOutput
from core.client.output.annotation_store import is_invalid_annotation_case
from core.tools.window_detector import get_active_window_info
from . import logger

from core.client.udp.udp_broadcaster import broadcast_output_udp
from core.tools.zhconv import convert as zhconv_convert
from core.client.audio.file_manager import AudioFileManager
from core.client.llm.llm_write_md import write_llm_md

if TYPE_CHECKING:
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient
    from core.client.hotword.manager import HotwordManager
    from core.client.diary.diary_writer import DiaryWriter



def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


class ResultProcessor:
    """
    识别结果处理器
    
    负责处理服务端返回的识别结果：
    - 接收 WebSocket 消息
    - 执行热词替换
    - 可选地调用 LLM 进行润色
    - 输出最终文本
    - 保存录音和日记
    """
    
    def __init__(self, app: CapsWriterClient):
        """
        初始化结果处理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._exit_event = asyncio.Event()
        self._loop = asyncio.get_running_loop()  # 保存事件循环引用

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def ws(self) -> WebSocketManager:
        """快捷访问连接管理器"""
        return self.app.ws

    @property
    def hotword(self) -> HotwordManager:
        """快捷访问热词管理器"""
        return self.app.hotword

    @property
    def output(self) -> TextOutput:
        """快捷访问文本输出器"""
        return self.app.output

    @property
    def diary(self) -> DiaryWriter:
        """快捷访问日记写入器"""
        return self.app.diary

    def request_exit(self):
        """请求退出处理循环（线程安全）"""
        logger.info("收到退出请求，设置退出事件")

        # 线程安全地设置事件
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._exit_event.set)
            logger.debug("已通过 call_soon_threadsafe 设置退出事件")
        else:
            self._exit_event.set()
            logger.debug("已直接设置退出事件")
    
    def _format_llm_result(self, llm_result) -> str:
        """格式化 LLM 结果输出"""
        polished_text = llm_result.result
        role_name = llm_result.role_name
        processed = llm_result.processed
        token_count = llm_result.token_count
        generation_time = llm_result.generation_time  # 使用生成时间（从第一个 token 开始）

        polished_text = polished_text.replace('\n', ' ').replace('\r', ' ')
        max_display_length = 50
        if len(polished_text) > max_display_length:
            polished_text = polished_text[:max_display_length] + '...'

        role_label = f'[{role_name}]' if role_name else ''
        result_text = f'[green]{polished_text}[/green]' if processed else polished_text

        if token_count == 0 and polished_text:
            token_count = _estimate_tokens(polished_text)

        # 使用生成时间计算速度（更准确）
        if processed and generation_time > 0:
            speed = token_count / generation_time if token_count > 0 else 0
            speed_label = f'    {speed:.1f} tokens/s' if speed > 0 else ''
        else:
            speed_label = ''

        return f'    模型结果{role_label}：{result_text}{speed_label}'
    
    def _log_modifier_key_state(self) -> None:
        """
        检测并记录当前按下的所有键

        用于调试按键卡住问题。
        macOS 上 import keyboard 会触发 CFDataValidateRange 断言导致崩溃，
        因此在 Darwin 平台直接跳过。
        """
        import platform
        if platform.system() == 'Darwin':
            return

        try:
            import keyboard

            # 获取所有当前按下的键
            pressed_keys = keyboard._pressed_events

            key_names = list(pressed_keys.keys())
            logger.debug(f"当前按下的键: {key_names}")

        except Exception as e:
            logger.debug(f"检测按键状态失败: {e}")
    
    async def start(self) -> None:
        """开启工作循环（含自动重联）。连接状态变化时通过 ErrorBus 更新 status.json。"""
        eb = getattr(self.app, 'error_bus', None)

        while not self._exit_event.is_set():
            # 1. 尝试连接，失败则重试
            if not await self.ws.connect():
                if eb:
                    eb.update(state='connecting', server_connected=False)
                await asyncio.sleep(2)
                continue

            # 连接成功：更新状态并发通知
            if eb:
                # 关键：有权限问题时绝不报「就绪/就位」——server 连上 ≠ 键盘接管可用。
                # 这里不再用权限探测去“猜”键盘是否可用，而是优先读取 bridge 的真实运行态：
                # 只有 active CGEventTap 已成功建立，客户端才算 ready；否则即便服务端连接成功，
                # 也只能算“识别引擎已连上，但键盘接管未就绪”。
                kbd_ok = True
                try:
                    import sys as _sys
                    if _sys.platform == 'darwin':
                        bridge = getattr(self.app, 'macos_caps_bridge', None)
                        if bridge is not None:
                            kbd_ok = bridge.is_tap_available()
                except Exception:
                    kbd_ok = True
                eb.update(
                    state='ready' if kbd_ok else 'error',
                    server_connected=True,
                )
                if kbd_ok:
                    eb.notify("识别引擎已连接，CapsWriter 就绪", "server_connected")
                else:
                    eb.notify(
                        "识别引擎已连接，但键盘接管未就绪，请按引导检查权限",
                        "server_connected_no_kbd",
                    )

            # 2. 消息接收循环
            while not self._exit_event.is_set():
                try:
                    message = await self.ws.receive()
                    if message is None:
                        break
                    await self._handle_message(message)
                except Exception as e:
                    logger.debug(f"连接异常中断: {e}")
                    break

            # 连接断开：更新状态并发通知
            if eb:
                eb.update(state='connecting', server_connected=False)
                eb.notify("识别引擎连接断开", "server_disconnected")

            console.print(f'[bold red]已断开服务端连接[/bold red]\n')
            self._cleanup()
            

    async def _handle_message(self, message: Optional[RecognitionMessage]) -> None:
        """处理接收到的消息"""
        if message is None:
            return


        # 使用 text 字段（简单拼接结果，用于语音输入）
        text = message.text
        original_text = text  # 保存原始识别结果
        delay = message.time_complete - message.time_submit
        trace_context = None

        if message.is_final:
            trace_context = self.state.pop_trace_context_by_task_id(message.task_id)
            logger.info(f"收到最终识别结果: {text}, 时延: {delay:.2f}s")
            if trace_context is not None:
                logger.info(
                    f"[trace {trace_context['trace_id']}] 最终识别结果已返回客户端: "
                    f"task_id={message.task_id}, "
                    f"shortcut_key={trace_context.get('shortcut_key')}, "
                    f"recording_start_time={trace_context.get('recording_start_time')}, "
                    f"first_audio_enqueue_time={trace_context.get('first_audio_enqueue_time')}, "
                    f"finish_requested_time={trace_context.get('finish_requested_time')}, "
                    f"cancel_requested_time={trace_context.get('cancel_requested_time')}, "
                    f"time_submit={message.time_submit}, "
                    f"time_complete={message.time_complete}"
                )
        else:
            logger.debug(
                f"接收到识别结果，文本: {text[:50]}{'...' if len(text) > 50 else ''}, "
                f"时延: {delay:.2f}s"
            )

        # 如果非最终结果，继续等待
        if not message.is_final:
            return

        # 繁体转换
        if Config.traditional_convert:
            try:
                text = zhconv_convert(text, Config.traditional_locale)
            except Exception as e:
                logger.warning(f"繁体转换失败: {e}")

        # 1. 音素检索，热词替换
        hotword_start = time.monotonic()
        correction_result = self.hotword.get_phoneme_corrector().correct(text, k=10)
        if Config.hot:
            text = correction_result.text

        # 2. 去掉末尾符号
        text = TextOutput.strip_punc(text)

        # 3. 正则替换
        text = self.hotword.get_rule_corrector().substitute(text)
        hotword_elapsed = time.monotonic() - hotword_start

        # 保存最近一次识别结果
        self.state.last_recognition_text = text

        # 控制台输出：时延 + 热词时延合并到一行
        hotword_label = f'  热词时延: {hotword_elapsed:.2f}s' if Config.hot else ''
        console.print(f'    转录时延：{delay:.2f}s{hotword_label}')

        # 先显示原始识别结果
        original_text_stripped = TextOutput.strip_punc(original_text)
        console.print(f'    识别结果：[green]{original_text_stripped}')

        # 如果发生了热词替换，显示替换后的结果
        if original_text_stripped != text:
            console.print(f'    热词替换：[cyan]{text}')
            logger.debug(f"热词替换后: {text[:50]}{'...' if len(text) > 50 else ''}")

        # 热词匹配情况
        matched_hotwords = correction_result.matchs
        potential_hotwords = correction_result.similars

        # 1. 显示完全匹配/已替换的热词
        if matched_hotwords and Config.hot:
            # 提取热词文本 (现为 (原词, 热词, 分数))
            replaced_info = [f"{origin}->[green4]{hw}[/]" for origin, hw, score in matched_hotwords]
            console.print(f'    完全匹配：{", ".join(replaced_info)}')

        # 2. 潜在热词记录到 log
        if potential_hotwords and Config.hot:
            replaced_set = {hw for origin, hw, score in matched_hotwords}
            potential_matches = [(origin, hw, score) for origin, hw, score in potential_hotwords if hw not in replaced_set]
            if potential_matches:
                log_str = "; ".join([f"{origin}->{hw}({score:.2f})" for origin, hw, score in potential_matches])
                logger.debug(f"潜在热词: {log_str}")

        # ===== 无效条闸门（2026-08-24，方案 A：只管标注域）=====
        # 命中：①时长 <0.5s（用户正常短句实测 0.70s 校准）②时长 <2s 且转录为空
        # （>2s 的空录音保留）。命中即视为误触发条：每次必弹通知（key 带 task_id
        # 防去重聚合）+ 不开编辑框 + 不登记 last_case；剪贴板/上屏/音频/日记等
        # 既有链路一律不动（落回直接输出路径，空文本由输出层既有守卫拦住）。
        # 录音时长：从 trace 上下文推（完成请求时刻 - 录音开始时刻），缺项则留空
        recording_duration = None
        if trace_context:
            _fin = trace_context.get('finish_requested_time')
            _beg = trace_context.get('recording_start_time')
            if _fin is not None and _beg is not None:
                recording_duration = _fin - _beg
        # 有 trace 时，即使本轮捕获结果为 None，也必须尊重该快照；只有历史消息或
        # 无 trace 来源的结果才回退全局值，避免 A 的晚到结果误用 B 的目标。
        source_app = (
            trace_context.get('paste_target')
            if trace_context is not None
            else getattr(self.state, 'paste_target', None)
        )
        # 必须用服务端原始转录判定，而非热词/规则处理后的 text；无效条是录音
        # 事实，不应被后处理规则偶然改写。判定函数同时被 AnnotationService 复用。
        invalid_case = is_invalid_annotation_case(original_text, recording_duration)
        if invalid_case:
            eb = getattr(self.app, 'error_bus', None)
            if eb is not None:
                try:
                    # key 拼 task_id：ErrorBus 同 key 30s 去重，必须保证每次误触发都弹
                    eb.notify('录音时间过短或为空，本条不计入标注系统',
                              f'invalid_case_{message.task_id}')
                except Exception as e:
                    logger.debug(f"[annotation] 无效条通知发送失败: {e}")
            console.print('    [yellow]录音时间过短或为空，本条不计入标注系统[/yellow]')

        # ===== 编辑框模式（macOS）：结果先进编辑框，确认后再上屏/存标注 =====
        # 仅拦截非 LLM 路径（LLM 有自己的输出管线）；面板不可用/已在显示/无效条
        # 时回退旧行为（无效条回退后也不登记 last_case）。
        use_editor = (
            sys.platform == 'darwin'
            and getattr(Config, 'editor_mode', False)
            and not Config.llm_enabled
            and not invalid_case
        )
        file_path_pending = None
        if Config.save_audio:
            # 音频路径必须在此时弹出（无论走哪条输出路径），编辑框确认/放弃回调里再重命名，
            # 避免直接输出路径先把文件移走导致标注案例拿不到音频
            file_path_pending = self.state.pop_audio_file(message.task_id)

        if use_editor:
            from core.client.output.edit_panel import present_editor
            import datetime as _dt
            # case 公共字段：确认/放弃回调共用（回调里只能拿到 dict，拿不到 message）
            case_common = {
                'ts': _dt.datetime.now().isoformat(timespec='seconds'),
                'task_id': message.task_id,
                'time_start': message.time_start,
                'raw_text': original_text,
                'recording_duration': recording_duration,
                'source_app': source_app,
                'audio_src': str(file_path_pending) if file_path_pending else None,
                'mode': 'editor',
            }
            # 注意：last_case 不在这里登记--「在框里等待编辑的不叫上一条」，
            # 只有 Enter/Esc 把面板关闭后才在回调里登记（kind 区分两种关闭方式）。

            def _on_confirm(final_text: str):
                # 面板关闭就是用户边界：先同步发布“上一条”，再派发可能包含磁盘 I/O
                # 的协程，确保用户紧接着触发标记时不会仍指向更早的案例。
                self._publish_editor_last_case(
                    case_common, final_text=final_text, kind='editor_confirmed')
                asyncio.run_coroutine_threadsafe(
                    self._editor_confirmed(dict(case_common), final_text, file_path_pending),
                    self.app.loop)

            def _on_cancel(panel_text: str):
                # Esc 只结束当前面板流程，不进入标注系统，也绝不能推进可标记
                # “上一条”指针；随后标记仍应命中 Esc 之前最近的合法案例。
                asyncio.run_coroutine_threadsafe(
                    self._editor_canceled(dict(case_common), file_path_pending, panel_text),
                    self.app.loop)

            if present_editor(text, _on_confirm, _on_cancel):
                return  # 上屏/改名/日记全部推迟到确认或放弃回调
            # 面板不可用/已占用：落回直接输出路径（下方继续，含 last_case 登记）

        # 窗口兼容性检测
        paste = Config.paste
        process_name = get_active_window_info().get('process_name', '').lower()
        if any(app.lower() == process_name for app in Config.paste_apps):
            paste = True
            logger.debug(f"检测到兼容性应用: {process_name}，使用粘贴模式")

        # LLM 处理和输出
        llm_result = None
        emit_succeeded = False
        if Config.llm_enabled:
            llm_result = await self.app.llm.process_and_output(
                text,
                paste=paste,
                matched_hotwords=potential_hotwords  # 传递上下文热词给 LLM
            )
        else:
            emit_succeeded = await self._emit_text(text, paste=paste)

        # direct 的用户边界是输出成功：必须先发布 provisional case，再做磁盘 I/O，
        # 否则归档较慢或失败时，“标记上一条”会误标更早的结果。
        register_direct = (
            not Config.llm_enabled and not invalid_case and bool(text) and emit_succeeded
        )
        if register_direct:
            import datetime as _dt0
            self.state.editor_last_case = {
                'ts': _dt0.datetime.now().isoformat(timespec='seconds'),
                'task_id': message.task_id,
                'time_start': message.time_start,
                'raw_text': original_text,
                'final_text': text,
                'recording_duration': recording_duration,
                'audio_src': str(file_path_pending) if file_path_pending else None,
                'source_app': source_app,
                'mode': 'direct', 'kind': 'direct', 'marked': False,
            }

        # 保存录音与写入 md 文件（直接输出路径；编辑框路径在回调里做同样的事）。
        # 方法内部已分项容错；外围仍保留保护，兼容测试替身或未来实现异常。
        try:
            file_audio = self._save_audio_and_diary(
                text, message.time_start, file_path_pending)
        except Exception as e:
            logger.error(f"[editor] 直接输出归档失败（不影响上一条）: {e}", exc_info=True)
            file_audio = None
        if register_direct and file_audio:
            self._backfill_last_case_audio(message.task_id, file_audio)

        # LLM 结果显示和保存
        if Config.llm_enabled and llm_result and llm_result.processed:
            console.print(self._format_llm_result(llm_result))
            write_llm_md(
                llm_result.input_text,
                llm_result.result,
                llm_result.role_name,
                message.time_start,
                file_audio
            )

        # 直接输出路径登记 last_case，供「标记上一条」热键/菜单使用。
        # （编辑框路径进入分支后已 return，不会走到这里；LLM 路径有自己的管线，
        # 不登记；无效条不登记--「上一条」保持为最新一条合法条。）
        # 边界口径：非编辑框模式下，能算「上一条」的分界线是写入剪贴板
        # （_emit_text 已成功完成，此处紧随其后）。
        # TextOutput.output 对空文本会直接返回，既不写剪贴板也不上屏；因此后处理
        # （去末尾标点/规则替换）得到空串时，不能越过“写入剪贴板后才算上一条”的
        # 直接输出边界，更不能用空结果覆盖用户仍可标记的旧案例。
        # 实际登记已提前到输出成功后；这里不再重复写入，避免覆盖期间发生的标记。

        # 检测修饰键状态（调试用）
        self._log_modifier_key_state()

        console.line()

    async def _emit_text(self, text: str, paste: Optional[bool] = None) -> bool:
        """统一输出出口（直接输出与编辑框确认两路共用）：上屏 + 记录输出文本 + UDP 广播。"""
        # output 只在文本已写入剪贴板（或打字成功）时返回 True；失败结果不得进入
        # 状态面板或 UDP，避免其他消费者把未实际输出的文本当作成功结果。
        if not await self.output.output(text, paste=paste):
            return False
        self.state.set_output_text(text)
        broadcast_output_udp(text)
        return True

    def _save_audio_and_diary(
        self, text: str, time_start: float, file_path_pending
    ) -> Optional[Path]:
        """保存录音与写日记（直接输出 / 编辑框确认 / 编辑框取消三路共用）。

        录音文件在 _handle_message 开头就已从 state 弹出到 file_path_pending，
        这里按各路径自己的最终文本重命名并写日记；返回重命名后的归档路径（无则 None）。
        保持旧行为：save_audio 开启但拿不到音频文件时，日记仍要写（file_audio=None）。
        """
        file_audio = None
        if Config.save_audio:
            if file_path_pending:
                try:
                    file_manager = AudioFileManager()
                    file_manager.file_path = Path(file_path_pending)
                    file_audio = file_manager.rename(text, time_start)
                except Exception as e:
                    # 音频改名失败不应阻止日记，更不能撤回已发布的上一条。
                    logger.error(f"[editor] 音频归档失败: {e}", exc_info=True)
            try:
                self.diary.write(text, time_start, file_audio)
            except Exception as e:
                # 日记是旁路记录；失败只记日志，不改变输出和标注交互语义。
                logger.error(f"[editor] 日记写入失败: {e}", exc_info=True)
        return file_audio

    def _publish_editor_last_case(self, case: dict, final_text: Optional[str], kind: str) -> None:
        """在面板关闭的同步边界幂等发布上一条，慢 I/O 不参与可见性判定。"""
        current = getattr(self.state, 'editor_last_case', None)
        if (current and current.get('task_id') == case.get('task_id')
                and current.get('kind') == kind):
            return
        self.state.editor_last_case = dict(
            case, final_text=final_text, kind=kind, marked=False,
            audio_src=case.get('audio_src'))

    def _backfill_last_case_audio(self, task_id: str, file_audio: Path) -> None:
        """仅给仍处于“上一条”的同一任务回填归档音频，绝不覆盖更新案例。"""
        current = getattr(self.state, 'editor_last_case', None)
        if not current or current.get('task_id') != task_id:
            return
        self.state.editor_last_case = dict(current, audio_src=str(file_audio))

    async def _editor_confirmed(self, case: dict, final_text: str, file_path_pending) -> None:
        """编辑框 Enter：立即发布上一条并优先上屏，随后完成旁路持久化。"""
        from core.client.output.edit_panel import activate_app_sync
        # message 在回调里不可得，time_start 随 case 传入；缺失时退回当前时间
        time_start = case.get('time_start') or time.time()
        self._publish_editor_last_case(
            case, final_text=final_text, kind='editor_confirmed')

        # 用户可见动作必须排在磁盘 I/O 前。音频重命名、日记追加和 v2 音频复制
        # 在真实机器上可能累计超过一秒，但都不应阻塞 Enter 到上屏的反馈。
        try:
            activate_app_sync(case.get('source_app'))
        except Exception as e:
            logger.error(f"[editor] 恢复目标应用失败（继续上屏）: {e}", exc_info=True)
        try:
            await self._emit_text(final_text, paste=True)
        except Exception as e:
            # 即使用户可见输出失败，corrected 数据仍需尽力持久化，便于追踪问题。
            logger.error(f"[editor] 确认文本输出失败（继续持久化）: {e}", exc_info=True)

        # 以下均为上屏后的旁路持久化；任一失败不得反向影响已经完成的用户动作。
        file_audio = None
        try:
            file_audio = self._save_audio_and_diary(final_text, time_start, file_path_pending)
        except Exception as e:
            logger.error(f"[editor] 确认后归档失败（继续标注）: {e}", exc_info=True)
        if file_audio:
            self._backfill_last_case_audio(case.get('task_id'), file_audio)
        # 标注音频优先用重命名后的归档路径；改名失败时退回待处理临时路径。
        audio_src = file_audio
        if audio_src is None and case.get('audio_src'):
            audio_src = Path(case['audio_src'])
        try:
            self.app.annotation.record(
                dict(case, status='corrected', final_text=final_text,
                     kind='editor_confirmed'),
                audio_src=audio_src,
            )
        except Exception as e:
            logger.error(f"[editor] corrected 标注写入失败（上屏已完成）: {e}", exc_info=True)

    async def _editor_canceled(self, case: dict, file_path_pending, panel_text: str) -> None:
        """编辑框 Esc 放弃（2026-08-24 口径）：
        - 不写标注数据集（等同该条没开编辑框模式）
        - 音频照常归档、日记照常写（既有基础设施不动）
        - 面板文本非空则写入剪贴板，但不自动上屏（TextOutput.output 在 Darwin
          强制 paste=True，故直接用 clipboard.safe_copy）
        - 不推进可标记“上一条”；标注指针保持 Esc 之前的案例
        - 恢复用户说话时的目标应用焦点
        """
        from core.client.output.edit_panel import activate_app_sync
        time_start = case.get('time_start') or time.time()
        text = (panel_text or '').strip()
        # 用户可见动作优先：非空先写剪贴板，再恢复原应用；慢归档放到后面，
        # 但整个路径始终不触碰 editor_last_case。
        try:
            if text:
                from core.client.clipboard.clipboard import safe_copy
                if safe_copy(text):
                    self.state.set_output_text(text)
                    logger.info(f"[editor] 已放弃上屏，转录已写入剪贴板 task={case.get('task_id')}")
        except Exception as e:
            logger.error(f"[editor] 放弃后写入剪贴板失败: {e}", exc_info=True)
        try:
            activate_app_sync(case.get('source_app'))
        except Exception as e:
            logger.error(f"[editor] 放弃后恢复目标应用失败: {e}", exc_info=True)
        try:
            self._save_audio_and_diary(
                text or (case.get('raw_text') or ''), time_start, file_path_pending)
        except Exception as e:
            logger.error(f"[editor] 放弃后归档失败（不影响剪贴板与焦点）: {e}", exc_info=True)
        logger.info(f"[editor] 用户放弃本条（不入标注库、不推进上一条）task={case.get('task_id')}")

    def _cleanup(self) -> None:
        """清理资源"""
        if self.state.websocket is not None:
            try:
                if self.state.websocket.closed:
                    self.state.websocket = None
                    logger.debug("WebSocket 连接已清理")
            except Exception:
                self.state.websocket = None

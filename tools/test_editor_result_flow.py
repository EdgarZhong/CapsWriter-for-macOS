# coding: utf-8 -*-
"""编辑框标注结果流隔离回归（2026-08-24 口径）。

本脚本仅以 fake app/state/output/annotation 和 unittest.mock 验证 ResultProcessor
副作用顺序；不启动完整客户端、不访问真实剪贴板、不写真实标注数据。覆盖：无效条
直出隔离、未知时长空文本、direct 登记时机，以及 Enter/Esc 两种面板关闭结果流。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

# 保证从任意 cwd 执行时都导入本项目，而不是环境中同名包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 本回归不触发真实剪贴板；测试环境未安装可选的 pyclip 时，先提供最小 API
# 让 ResultProcessor 能被导入，避免无关依赖遮蔽结果流语义断言。
if 'pyclip' not in sys.modules:
    _pyclip_stub = types.ModuleType('pyclip')
    _pyclip_stub.copy = lambda content: None
    _pyclip_stub.paste = lambda: ''
    sys.modules['pyclip'] = _pyclip_stub

# 导入 AudioFileManager 会经由 audio 包初始化触及 sounddevice；本回归禁用音频归档，
# 因此空替身足以通过模块加载，不会代替任何实际录音或设备行为。
if 'sounddevice' not in sys.modules:
    sys.modules['sounddevice'] = types.ModuleType('sounddevice')

from config_client import ClientConfig as Config
from core.protocol import RecognitionMessage


class _FakeErrorBus:
    """只记录通知文本和 key，用于确认误触发通知不会被同 key 聚合。"""

    def __init__(self):
        self.notifications = []

    def notify(self, message, key):
        self.notifications.append((message, key))


class _FakeState:
    """结果流需要的最小 state；登记上一条时写入 events 以验证调用顺序。"""

    def __init__(self):
        object.__setattr__(self, 'events', [])
        object.__setattr__(self, 'traces', {})
        object.__setattr__(self, 'editor_last_case', None)
        self.last_recognition_text = None
        self.paste_target = {'pid': 42, 'name': '目标应用'}
        self.output_texts = []

    def __setattr__(self, name, value):
        if name == 'editor_last_case' and hasattr(self, 'events') and value is not None:
            self.events.append('last_case')
        object.__setattr__(self, name, value)

    def pop_trace_context_by_task_id(self, task_id):
        return self.traces.pop(task_id, None)

    def pop_audio_file(self, task_id):
        return None

    def set_output_text(self, text):
        self.output_texts.append(text)


class _FakeCorrector:
    """让热词阶段保持输入原样，隔离结果流而非热词算法。"""

    def correct(self, text, k):
        return type('Correction', (), {'text': text, 'matchs': [], 'similars': []})()

    def substitute(self, text):
        return text


class _FakeHotword:
    def get_phoneme_corrector(self):
        return _FakeCorrector()

    def get_rule_corrector(self):
        return _FakeCorrector()


class _FakeAnnotation:
    """记录 record 发生点，验证 Enter 必须先存 corrected 再登记上一条。"""

    def __init__(self, events):
        self.events = events
        self.records = []

    def record(self, case, audio_src=None):
        self.events.append('record')
        self.records.append((case, audio_src))
        return {'skipped': False}


class _FakeApp:
    def __init__(self, loop):
        self.loop = loop
        self.state = _FakeState()
        self.error_bus = _FakeErrorBus()
        self.hotword = _FakeHotword()
        self.annotation = _FakeAnnotation(self.state.events)
        self.output = object()
        self.diary = object()


def _message(task_id, text):
    """构造满足协议字段的最终识别消息；时长由 trace 决定而非 message.duration。"""
    return RecognitionMessage(
        task_id=task_id, is_final=True, duration=0.0, time_start=10.0,
        time_submit=11.0, time_complete=12.0, text=text,
    )


def _set_trace(state, task_id, duration, paste_target=None):
    """duration=None 模拟 trace 缺项；可附带本轮录音开始时捕获的上屏目标。"""
    if duration is not None:
        state.traces[task_id] = {
            'trace_id': f'trace-{task_id}', 'recording_start_time': 100.0,
            'finish_requested_time': 100.0 + duration,
            'paste_target': paste_target,
        }


async def _new_processor():
    """ResultProcessor 构造时需要运行中的 asyncio loop，故统一在协程中创建。"""
    from core.client.output.result_processor import ResultProcessor

    app = _FakeApp(asyncio.get_running_loop())
    processor = ResultProcessor(app)
    processor._emit_text = AsyncMock()
    return processor, app


async def case_invalid_short_text_direct_and_keeps_last():
    """0.3s 非空：每条通知 key 独立、不开面板、仍直出、旧上一条不被覆盖。"""
    processor, app = await _new_processor()
    old_case = {'task_id': 'old', 'kind': 'direct', 'marked': False}
    app.state.editor_last_case = old_case
    app.state.events.clear()
    _set_trace(app.state, 'short-a', 0.3)
    _set_trace(app.state, 'short-b', 0.3)

    with patch.multiple(Config, editor_mode=True, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.sys.platform', 'darwin'), \
            patch('core.client.output.result_processor.get_active_window_info', return_value={}), \
            patch('core.client.output.edit_panel.present_editor') as present_editor:
        await processor._handle_message(_message('short-a', '嗯'))
        await processor._handle_message(_message('short-b', '啊'))

    assert present_editor.call_count == 0, '无效短录音不得打开编辑框'
    assert processor._emit_text.await_args_list[0].args[0] == '嗯'
    assert processor._emit_text.await_args_list[1].args[0] == '啊'
    assert app.state.editor_last_case is old_case, '无效条不得覆盖旧 editor_last_case'
    keys = [key for _, key in app.error_bus.notifications]
    assert keys == ['invalid_case_short-a', 'invalid_case_short-b'], keys
    assert all('录音时间过短或为空' in message for message, _ in app.error_bus.notifications)
    print('  case_invalid_short_text_direct_and_keeps_last: PASS')


async def case_invalid_empty_keeps_last():
    """1.2s 空文本：不开面板且不覆盖上一条；输出层仍自行处理空文本。"""
    processor, app = await _new_processor()
    old_case = {'task_id': 'old-empty', 'kind': 'direct', 'marked': False}
    app.state.editor_last_case = old_case
    app.state.events.clear()
    _set_trace(app.state, 'empty-short', 1.2)

    with patch.multiple(Config, editor_mode=True, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.sys.platform', 'darwin'), \
            patch('core.client.output.result_processor.get_active_window_info', return_value={}), \
            patch('core.client.output.edit_panel.present_editor') as present_editor:
        await processor._handle_message(_message('empty-short', ''))

    assert present_editor.call_count == 0, '已知短时长空文本不得打开编辑框'
    assert app.state.editor_last_case is old_case, '无效空条不得覆盖旧 editor_last_case'
    assert len(app.error_bus.notifications) == 1
    print('  case_invalid_empty_keeps_last: PASS')


async def case_unknown_empty_enters_editor():
    """时长未知＋空文本不是无效条，允许进入编辑框路径且不发无效通知。"""
    processor, app = await _new_processor()

    with patch.multiple(Config, editor_mode=True, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.sys.platform', 'darwin'), \
            patch('core.client.output.edit_panel.present_editor', return_value=True) as present_editor:
        await processor._handle_message(_message('unknown-empty', ''))

    assert present_editor.call_count == 1, '时长未知空文本应允许进入编辑框'
    assert app.error_bus.notifications == [], '时长未知空文本不得触发无效通知'
    assert processor._emit_text.await_count == 0, '面板接管后不应立即直出'
    print('  case_unknown_empty_enters_editor: PASS')


async def case_direct_registers_after_emit():
    """有效 direct：只有 _emit_text 完成后，才可登记 kind=direct 的上一条。"""
    processor, app = await _new_processor()
    events = app.state.events

    async def _emit(text, paste=None):
        events.append('emit')
        return True

    processor._emit_text.side_effect = _emit
    processor._save_audio_and_diary = lambda *args: events.append('io')
    _set_trace(app.state, 'direct', 2.5)
    with patch.multiple(Config, editor_mode=False, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.get_active_window_info', return_value={}):
        await processor._handle_message(_message('direct', '直接输出'))

    assert events == ['emit', 'last_case', 'io'], events
    assert app.state.editor_last_case['kind'] == 'direct'
    print('  case_direct_registers_after_emit: PASS')


async def case_paste_target_isolated_by_trace():
    """A/B 录音并发时，A 的晚到结果必须继续使用 A 开始时捕获的目标。"""
    from core.client.state import ClientState

    target_a = {'pid': 101, 'name': '应用 A'}
    target_b = {'pid': 202, 'name': '应用 B'}
    state = ClientState()
    state.start_recording(10.0, trace_id='trace-a', paste_target=target_a)
    state.bind_task_trace('task-a', 'trace-a')
    state.start_recording(20.0, trace_id='trace-b', paste_target=target_b)
    state.bind_task_trace('task-b', 'trace-b')
    assert state.pop_trace_context_by_task_id('task-a')['paste_target'] == target_a
    assert state.pop_trace_context_by_task_id('task-b')['paste_target'] == target_b

    processor, app = await _new_processor()
    app.state.paste_target = target_b
    _set_trace(app.state, 'late-a', 2.5, paste_target=target_a)
    processor._emit_text.return_value = True
    with patch.multiple(Config, editor_mode=False, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.get_active_window_info', return_value={}):
        await processor._handle_message(_message('late-a', '来自 A'))
    assert app.state.editor_last_case['source_app'] == target_a
    print('  case_paste_target_isolated_by_trace: PASS')


async def case_direct_empty_after_processing_keeps_last():
    """后处理文本为空时输出层不会写剪贴板，不能把该条登记为 direct。"""
    processor, app = await _new_processor()
    old_case = {'task_id': 'old-after-processing', 'kind': 'direct', 'marked': False}
    app.state.editor_last_case = old_case
    app.state.events.clear()
    _set_trace(app.state, 'direct-empty', 2.5)

    # 模拟 strip_punc/规则替换后只剩空串；真实 TextOutput.output 会对它直接 return，
    # 本隔离测试只验证调用方不会越过“写入剪贴板后才算上一条”的登记边界。
    with patch.multiple(Config, editor_mode=False, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.TextOutput.strip_punc', return_value=''), \
            patch('core.client.output.result_processor.get_active_window_info', return_value={}):
        await processor._handle_message(_message('direct-empty', '只剩标点'))

    processor._emit_text.assert_awaited_once_with('', paste=Config.paste)
    assert app.state.editor_last_case is old_case, '未写剪贴板的空结果不得覆盖旧 editor_last_case'
    assert app.state.events == [], app.state.events
    print('  case_direct_empty_after_processing_keeps_last: PASS')


async def case_paste_copy_failure_does_not_send_paste():
    """剪贴板写入失败时不得继续发送 Cmd+V，且输出结果必须明确失败。"""
    from core.client.clipboard import clipboard

    with patch('core.client.clipboard.clipboard.platform.system', return_value='Darwin'), \
            patch('core.client.clipboard.clipboard.safe_copy', return_value=False) as safe_copy, \
            patch('core.client.clipboard.clipboard.subprocess.run') as run:
        copied = await clipboard.paste_text('复制失败', restore_clipboard=False)

    assert copied is False
    safe_copy.assert_called_once_with('复制失败')
    run.assert_not_called()
    print('  case_paste_copy_failure_does_not_send_paste: PASS')


async def case_macos_paste_permission_failure_keeps_copy_success():
    """macOS 无辅助功能权限时，文本已入剪贴板仍视为输出成功。"""
    from core.client.clipboard import clipboard

    failed_paste = type('_Result', (), {'returncode': 1, 'stderr': b'not allowed'})()
    with patch('core.client.clipboard.clipboard.platform.system', return_value='Darwin'), \
            patch('core.client.clipboard.clipboard.safe_copy', return_value=True), \
            patch('core.client.clipboard.clipboard._post_macos_paste_shortcut',
                  return_value=False), \
            patch('core.client.clipboard.clipboard.subprocess.run', return_value=failed_paste):
        copied = await clipboard.paste_text('可手动粘贴', restore_clipboard=False)

    assert copied is True
    print('  case_macos_paste_permission_failure_keeps_copy_success: PASS')


async def case_macos_native_paste_has_no_fixed_wait():
    """macOS 正常路径必须走进程内 Quartz，不能再启动 osascript 或固定 sleep。"""
    from core.client.clipboard import clipboard

    with patch('core.client.clipboard.clipboard.platform.system', return_value='Darwin'), \
            patch('core.client.clipboard.clipboard.safe_copy', return_value=True), \
            patch('core.client.clipboard.clipboard._post_macos_paste_shortcut',
                  return_value=True) as native_paste, \
            patch('core.client.clipboard.clipboard.subprocess.run') as run, \
            patch('core.client.clipboard.clipboard.asyncio.sleep',
                  new_callable=AsyncMock) as sleep:
        copied = await clipboard.paste_text('立即上屏', restore_clipboard=True)

    assert copied is True
    native_paste.assert_called_once_with()
    run.assert_not_called()
    sleep.assert_not_awaited()
    print('  case_macos_native_paste_has_no_fixed_wait: PASS')


async def case_emit_failure_skips_state_and_udp():
    """输出层失败时，不得把未写入剪贴板的文本伪装成已输出。"""
    processor, app = await _new_processor()
    app.output = type('_FakeOutput', (), {'output': AsyncMock(return_value=False)})()

    with patch('core.client.output.result_processor.broadcast_output_udp') as broadcast:
        # _new_processor 为其余结果流用例替换了实例方法；此处显式调用类实现，
        # 才能验证真实的成功状态闸门。
        emitted = await type(processor)._emit_text(processor, '未成功输出', paste=True)

    assert emitted is False
    assert app.state.output_texts == []
    broadcast.assert_not_called()
    print('  case_emit_failure_skips_state_and_udp: PASS')


async def case_direct_emit_failure_keeps_last():
    """direct 输出失败时，不能覆盖用户仍可标记的旧上一条。"""
    processor, app = await _new_processor()
    old_case = {'task_id': 'old-failed-direct', 'kind': 'direct', 'marked': False}
    app.state.editor_last_case = old_case
    app.state.events.clear()
    processor._emit_text.return_value = False
    _set_trace(app.state, 'direct-failed', 2.5)

    with patch.multiple(Config, editor_mode=False, llm_enabled=False, save_audio=False,
                        hot=False), \
            patch('core.client.output.result_processor.get_active_window_info', return_value={}):
        await processor._handle_message(_message('direct-failed', '输出失败'))

    assert app.state.editor_last_case is old_case
    assert app.state.events == []
    print('  case_direct_emit_failure_keeps_last: PASS')


async def case_editor_confirmed_order():
    """Enter：先发布上一条并立即恢复/上屏，慢归档与 corrected 落盘必须后置。"""
    processor, app = await _new_processor()
    events = app.state.events

    async def _emit(text, paste=None):
        events.append(('emit', text, paste))

    processor._emit_text.side_effect = _emit
    def _persist(*_args):
        events.append('persist')
        return None

    processor._save_audio_and_diary = _persist
    case = {'task_id': 'confirm', 'raw_text': '原文', 'time_start': 10.0, 'mode': 'editor'}
    with patch('core.client.output.edit_panel.activate_app_sync',
               side_effect=lambda target: events.append('activate')):
        await processor._editor_confirmed(case, '修正后', None)

    assert app.annotation.records[0][0]['status'] == 'corrected'
    assert app.annotation.records[0][0]['kind'] == 'editor_confirmed'
    assert events == [
        'last_case', 'activate', ('emit', '修正后', True), 'persist', 'record'
    ], events
    assert app.state.editor_last_case['kind'] == 'editor_confirmed'
    print('  case_editor_confirmed_order: PASS')


async def case_editor_callbacks_publish_before_dispatch():
    """Enter 立即发布；Esc 只派发清理协程，必须保留此前可标记上一条。"""
    for action, expected_kind in (('confirm', 'editor_confirmed'), ('cancel', 'direct')):
        processor, app = await _new_processor()
        previous = {'task_id': 'previous', 'kind': 'direct', 'marked': False}
        if action == 'cancel':
            app.state.editor_last_case = previous
            app.state.events.clear()
        held_coroutines = []

        def _hold(coro, _loop):
            held_coroutines.append(coro)
            return object()

        def _present(_text, on_confirm, on_cancel):
            if action == 'confirm':
                on_confirm('修正后')
            else:
                on_cancel('放弃文本')
            assert app.state.editor_last_case['kind'] == expected_kind
            if action == 'cancel':
                assert app.state.editor_last_case is previous
            assert held_coroutines, '发布上一条后才应派发异步 I/O'
            return True

        try:
            with patch.multiple(Config, editor_mode=True, llm_enabled=False,
                                save_audio=False, hot=False), \
                    patch('core.client.output.result_processor.sys.platform', 'darwin'), \
                    patch('core.client.output.edit_panel.present_editor', side_effect=_present), \
                    patch('core.client.output.result_processor.asyncio.run_coroutine_threadsafe',
                          side_effect=_hold):
                await processor._handle_message(_message(f'callback-{action}', '原文'))
        finally:
            # 测试刻意不执行慢协程；显式关闭，避免未 await 警告干扰结果。
            for coro in held_coroutines:
                coro.close()

    print('  case_editor_callbacks_publish_before_dispatch: PASS')


async def case_editor_io_failures_do_not_block_output():
    """归档或标注失败不能撤回上一条，也不能阻断 Enter 上屏与 Esc 写剪贴板。"""
    processor, app = await _new_processor()
    processor._save_audio_and_diary = Mock(side_effect=OSError('归档失败'))
    app.annotation.record = Mock(side_effect=RuntimeError('标注失败'))
    processor._emit_text.return_value = True
    confirmed = {'task_id': 'confirm-io', 'raw_text': '原文', 'time_start': 10.0,
                 'mode': 'editor', 'source_app': {'pid': 101}}
    with patch('core.client.output.edit_panel.activate_app_sync'):
        await processor._editor_confirmed(confirmed, '仍需上屏', None)
    processor._emit_text.assert_awaited_once_with('仍需上屏', paste=True)
    assert app.state.editor_last_case['task_id'] == 'confirm-io'

    processor, app = await _new_processor()
    processor._save_audio_and_diary = Mock(side_effect=OSError('日记失败'))
    previous = {'task_id': 'before-cancel', 'kind': 'direct', 'marked': False}
    app.state.editor_last_case = previous
    canceled = {'task_id': 'cancel-io', 'raw_text': '原文', 'time_start': 10.0,
                'mode': 'editor', 'source_app': {'pid': 202}}
    with patch('core.client.clipboard.clipboard.safe_copy', return_value=True) as safe_copy, \
            patch('core.client.output.edit_panel.activate_app_sync') as activate:
        await processor._editor_canceled(canceled, None, '仍需复制')
    safe_copy.assert_called_once_with('仍需复制')
    activate.assert_called_once_with({'pid': 202})
    assert app.state.editor_last_case is previous
    print('  case_editor_io_failures_do_not_block_output: PASS')


async def case_audio_backfill_keeps_newer_last_case():
    """A 的慢归档晚于 B 发布完成时，只能放弃回填，不能覆盖 B。"""
    processor, app = await _new_processor()
    newer = {'task_id': 'task-b', 'kind': 'direct', 'marked': False,
             'audio_src': 'b.wav'}
    app.state.editor_last_case = newer
    app.state.events.clear()
    processor._backfill_last_case_audio('task-a', Path('/tmp/a.wav'))
    assert app.state.editor_last_case is newer
    assert app.state.events == []
    print('  case_audio_backfill_keeps_newer_last_case: PASS')


async def case_editor_canceled_clipboard_only():
    """Esc：非空只复制、清空不覆盖；两者均不落盘、不推进上一条。"""
    processor, app = await _new_processor()
    processor._save_audio_and_diary = lambda *args: None
    previous = {'task_id': 'previous-cancel', 'kind': 'editor_confirmed', 'marked': False}
    app.state.editor_last_case = previous
    case = {'task_id': 'cancel', 'raw_text': '原文', 'time_start': 10.0,
            'mode': 'editor', 'source_app': {'pid': 303}}

    with patch('core.client.clipboard.clipboard.safe_copy', return_value=True) as safe_copy, \
            patch('core.client.output.edit_panel.activate_app_sync') as activate:
        await processor._editor_canceled(case, None, '保留在剪贴板')

    assert app.annotation.records == [], 'Esc 不得写 annotation.record'
    safe_copy.assert_called_once_with('保留在剪贴板')
    assert processor._emit_text.await_count == 0, 'Esc 不得自动上屏'
    activate.assert_called_once_with({'pid': 303})
    assert app.state.editor_last_case is previous
    assert app.state.output_texts == ['保留在剪贴板']

    # 用户把面板清空后 Esc，不得用空串覆盖现有系统剪贴板；焦点和指针规则不变。
    with patch('core.client.clipboard.clipboard.safe_copy') as safe_copy_empty, \
            patch('core.client.output.edit_panel.activate_app_sync') as activate_empty:
        await processor._editor_canceled(case, None, '')
    safe_copy_empty.assert_not_called()
    activate_empty.assert_called_once_with({'pid': 303})
    assert app.state.editor_last_case is previous
    assert app.state.output_texts == ['保留在剪贴板']
    print('  case_editor_canceled_clipboard_only: PASS')


async def main():
    await case_invalid_short_text_direct_and_keeps_last()
    await case_invalid_empty_keeps_last()
    await case_unknown_empty_enters_editor()
    await case_direct_registers_after_emit()
    await case_paste_target_isolated_by_trace()
    await case_direct_empty_after_processing_keeps_last()
    await case_paste_copy_failure_does_not_send_paste()
    await case_macos_paste_permission_failure_keeps_copy_success()
    await case_macos_native_paste_has_no_fixed_wait()
    await case_emit_failure_skips_state_and_udp()
    await case_direct_emit_failure_keeps_last()
    await case_editor_confirmed_order()
    await case_editor_callbacks_publish_before_dispatch()
    await case_editor_io_failures_do_not_block_output()
    await case_audio_backfill_keeps_newer_last_case()
    await case_editor_canceled_clipboard_only()
    print('editor 结果流全部断言通过 ✅')


if __name__ == '__main__':
    asyncio.run(main())

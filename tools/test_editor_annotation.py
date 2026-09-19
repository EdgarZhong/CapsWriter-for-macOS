# coding: utf-8 -*-
"""AnnotationService 标注落盘隔离测试（2026-08-23 创建，2026-08-24 语义重定义）。

场景：
1. v2 物理隔离：新版路径、固定版本字段和音频相对路径；伪造旧 v1 JSONL、
   音频哨兵的文件清单与字节逐项不变；
2. record 正常写 JSONL + 拷贝音频到 audio/；
3. record audio_src=None 不崩、audio_file 为 null；
4. 无效案例过滤（时长 <0.5s / 已知时长 <2s 且空文本）不入库；
   时长未知不能仅凭空文本判成无效条；
5. mark_last_problem 语义分流：editor_confirmed -> final_unreliable（真值不可靠，
   通知摘录取 final）；direct / editor_canceled -> raw_unreliable（转录有误）；
   无案例拒绝 / 重复标记去重 / 通知带内容摘录；
6. record 落盘异常（root 不可写）不上抛，mark_last_problem 返回 write_failed
   且不置去重标记（可重试）。
7. 两个线程并发标记同一案例时，check→record→marked 事务只成功一次；
8. 真实 ErrorBus 在 30 秒窗口内不会聚合两个不同任务的成功通知。

测试通过假 app.base_dir 把 root 定位到临时目录，绝不写真实 evals/manual_cases。
用法：项目根目录下 `.venv/bin/python tools/test_editor_annotation.py`
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

# 保证从任意 cwd 运行都能导入项目包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeEB:
    def __init__(self):
        self.msgs = []

    def notify(self, m, k):
        self.msgs.append((m, k))


class _FakeState:  # 仅承载 editor_last_case
    def __init__(self):
        self.editor_last_case = None


class _FakeApp:
    def __init__(self):
        self.state = _FakeState()
        self.error_bus = _FakeEB()

    @property
    def base_dir(self):
        return Path(self._tmp)


def _make_svc(base: Path):
    from core.client.output.annotation_store import AnnotationService

    app = _FakeApp()
    app._tmp = str(base)
    return AnnotationService(app), app


def case_v2_physical_isolation(tmp: Path):
    """场景 1：新版只写 v2；伪造旧 v1 元数据必须逐字节保持不变。

    此测试刻意只在 tempfile 创建假旧文件，既证明新版不会误写旧目录，
    也避免测试读取、打印或修改任何真实个人标注与音频内容。
    """
    # 先验证全新安装只会创建 v2，不会意外创建旧根目录 JSONL。
    fresh_base = tmp / 'fresh'
    fresh_svc, _ = _make_svc(fresh_base)
    assert fresh_svc.root == fresh_base / 'evals' / 'manual_cases' / 'v2'
    fresh_svc.record({'task_id': 'fresh-v2', 'raw_text': '新数据', 'recording_duration': 3.0})
    assert not (fresh_base / 'evals' / 'manual_cases' / 'cases.jsonl').exists()

    # 再单独伪造旧 v1 JSONL 与 audio 哨兵，验证写 v2 后旧资产逐字节不变。
    legacy_base = tmp / 'legacy'
    old_jsonl = legacy_base / 'evals' / 'manual_cases' / 'cases.jsonl'
    old_jsonl.parent.mkdir(parents=True)
    old_bytes = b'{"legacy":"v1 bytes must remain unchanged"}\n'
    old_jsonl.write_bytes(old_bytes)
    old_audio_dir = old_jsonl.parent / 'audio'
    old_audio_dir.mkdir()
    old_audio_sentinel = old_audio_dir / 'legacy-sentinel.flac'
    old_audio_sentinel.write_bytes(b'legacy-audio-bytes-must-remain-unchanged')
    # 快照同时覆盖文件名与内容，证明新版既不改写也不在旧目录新增音频。
    old_audio_snapshot = {
        path.name: path.read_bytes() for path in old_audio_dir.iterdir()
    }

    svc, _ = _make_svc(legacy_base)
    src = legacy_base / 'fake.mp3'
    src.write_bytes(b'ID3-v2-isolation')
    entry = svc.record(
        {
            'ts': '2026-08-24T12:00:00',
            'task_id': 'v2-case',
            'status': 'corrected',
            # 调用方即使伪造旧版本，服务端也必须强制写当前 v2 格式。
            'annotation_version': 1,
            'raw_text': '新版原始文本',
            'final_text': '新版确认文本',
            'recording_duration': 3.0,
        },
        audio_src=src,
    )

    assert svc.root == legacy_base / 'evals' / 'manual_cases' / 'v2'
    assert entry['annotation_version'] == 2
    assert entry['audio_file'] and entry['audio_file'].startswith('audio/')
    assert old_jsonl.read_bytes() == old_bytes, '旧 v1 JSONL 必须逐字节保持不变'
    assert {path.name: path.read_bytes() for path in old_audio_dir.iterdir()} == \
        old_audio_snapshot, '旧 v1 audio 文件清单与哨兵字节必须完全不变'
    assert (svc.root / entry['audio_file']).is_file(), '新版音频必须只写入 v2/audio'
    print('  case_v2_physical_isolation: PASS')


def case_record_and_copy(tmp: Path):
    """场景 2：record 正常写 JSONL + 拷贝音频 + 返回含相对路径的 entry"""
    svc, _ = _make_svc(tmp)
    src = tmp / 'fake.mp3'
    src.write_bytes(b'ID3xxxx')
    e1 = svc.record(
        {
            'ts': '2026-08-23T12:00:00',
            'task_id': 'tid-0001',
            'status': 'corrected',
            'kind': 'editor_confirmed',
            'raw_text': '原始',
            'final_text': '纠正后',
            'recording_duration': 5.2,
            'source_app': {'pid': 123, 'name': 'Safari'},
        },
        audio_src=src,
    )
    e2 = svc.record(
        {
            'ts': '2026-08-23T12:01:00',
            'task_id': 'tid-0002',
            'status': 'raw_unreliable',
            'kind': 'direct',
            'raw_text': '原始2',
            'final_text': None,
            'recording_duration': 3.1,
            'source_app': None,
        },
        audio_src=src,
    )
    lines = svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 2, f'应有两行 JSONL，实际 {len(lines)}'
    assert json.loads(lines[0])['final_text'] == '纠正后'
    assert json.loads(lines[1])['status'] == 'raw_unreliable'
    assert 'write_ok' not in json.loads(lines[0]), '调用控制字段不得污染 JSONL 格式'
    assert e1['write_ok'] is True and e2['write_ok'] is True
    # JSONL 中文不转义（ensure_ascii=False）
    assert '原始' in lines[0]
    assert e1['audio_file'] and (svc.root / e1['audio_file']).exists(), '音频应被拷贝'
    assert e2['audio_file'] and e2['audio_file'] != e1['audio_file'], '两次拷贝文件名应不同'
    print('  case_record_and_copy: PASS')


def case_record_no_audio(tmp: Path):
    """场景 3：audio_src=None 不崩、audio_file 为 null"""
    svc, _ = _make_svc(tmp)
    e = svc.record({'task_id': 'tid-x', 'status': 'corrected', 'raw_text': 'r',
                    'final_text': 'f', 'recording_duration': 4.0},
                   audio_src=None)
    assert e['audio_file'] is None, 'audio_src=None 时 audio_file 应为 null'
    lines = svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])['audio_file'] is None
    # audio_src 指向不存在文件同样跳过拷贝
    e2 = svc.record({'task_id': 'tid-y', 'raw_text': 'r2', 'recording_duration': 4.0},
                    audio_src=tmp / 'nope.mp3')
    assert e2['audio_file'] is None
    print('  case_record_no_audio: PASS')


def case_invalid_filtered(tmp: Path):
    """场景 4：无效案例（过短/为空）不入库（record 层兜底过滤）"""
    svc, _ = _make_svc(tmp)
    # ①时长 <0.5s（即使非空）
    r1 = svc.record({'task_id': 't1', 'raw_text': '嗯', 'recording_duration': 0.3})
    # ②时长 <2s 且空文本
    r2 = svc.record({'task_id': 't2', 'raw_text': '', 'recording_duration': 1.2})
    assert r1.get('skipped') and r2.get('skipped'), \
        f'两个无效案例都应跳过，实际 {r1} {r2}'
    # ③时长未知不能仅凭空文本判成无效条：trace 缺失不是误触发的证据。
    unknown = svc.record({'task_id': 'unknown', 'raw_text': ''})
    assert not unknown.get('skipped'), '时长未知不能仅凭空文本判成无效条'
    # 边界外：>2s 空录音保留（真实收音问题）；0.70s 正常短句保留
    r4 = svc.record({'task_id': 't4', 'raw_text': '', 'recording_duration': 3.0})
    r5 = svc.record({'task_id': 't5', 'raw_text': '可以', 'recording_duration': 0.70})
    assert not r4.get('skipped') and not r5.get('skipped'), '边界外案例应正常入库'
    lines = svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 3, f'仅三条合法案例应落盘，实际 {len(lines)} 条'
    print('  case_invalid_filtered: PASS')


def case_mark_last_problem(tmp: Path):
    """场景 5：两种标记语义分流 + 摘录 + 去重 + 拒绝"""
    svc, app = _make_svc(tmp)
    src = tmp / 'fake.mp3'
    src.write_bytes(b'ID3xxxx')

    # 无案例 -> 拒绝
    r0 = svc.mark_last_problem()
    assert r0['ok'] is False and r0['reason'] == 'no_case'
    assert app.error_bus.msgs == [], '拒绝不应发通知'

    # editor_confirmed（编辑框 Enter 确认条）-> final_unreliable + 通知摘录取 final
    app.state.editor_last_case = {
        'ts': '2026-08-23T12:02:00',
        'task_id': 'tid-0003',
        'raw_text': '原始识别文本比较长需要截断的样子',
        'final_text': '用户纠正后的真值文本也比较长需要截断',
        'recording_duration': 5.0,
        'audio_src': str(src),
        'source_app': None,
        'mode': 'editor', 'kind': 'editor_confirmed',
    }
    r1 = svc.mark_last_problem()
    assert r1['ok'] is True, f'实际 {r1}'
    lines = svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()
    e = json.loads(lines[-1])
    assert e['status'] == 'final_unreliable', e
    assert e['final_text'] == '用户纠正后的真值文本也比较长需要截断'
    assert app.error_bus.msgs, '标记成功应发通知'
    msg = app.error_bus.msgs[-1][0]
    assert '真值不可靠' in msg and '用户纠正后的真值'[:10] in msg, msg

    # 即使用户清空编辑框再 Enter，该条仍是 editor_confirmed：标记语义只由
    # kind 决定，必须写 final_unreliable；通知因空 final 回退摘录 raw。
    app.state.editor_last_case = {
        'ts': '2026-08-23T12:02:30',
        'task_id': 'tid-0003-empty-final',
        'raw_text': '用户清空前的原始转录',
        'final_text': '',
        'recording_duration': 5.0,
        'audio_src': str(src),
        'source_app': None,
        'mode': 'editor', 'kind': 'editor_confirmed',
    }
    r_empty_final = svc.mark_last_problem()
    assert r_empty_final['ok'] is True, f'实际 {r_empty_final}'
    e = json.loads(svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()[-1])
    assert e['status'] == 'final_unreliable' and e['final_text'] == '', e
    msg = app.error_bus.msgs[-1][0]
    assert '真值不可靠' in msg and '用户清空前的原始转录' in msg, msg

    # 重复标记 -> 去重
    r2 = svc.mark_last_problem()
    assert r2['ok'] is False and r2['reason'] == 'already_marked'
    n_lines = len(svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines())

    # editor_canceled（Esc 放弃条，只有 raw）-> raw_unreliable + 通知摘录取 raw
    app.state.editor_last_case = {
        'ts': '2026-08-23T12:03:00',
        'task_id': 'tid-0004',
        'raw_text': '被放弃那条的原始转录',
        'final_text': None,
        'recording_duration': 4.0,
        'audio_src': str(src),
        'source_app': None,
        'mode': 'editor', 'kind': 'editor_canceled',
    }
    r3 = svc.mark_last_problem()
    assert r3['ok'] is True, f'实际 {r3}'
    lines = svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()
    e = json.loads(lines[-1])
    assert e['status'] == 'raw_unreliable' and e['final_text'] is None, e
    msg = app.error_bus.msgs[-1][0]
    assert '转录有误' in msg and '被放弃那条' in msg, msg

    # direct（非编辑框条）-> 同样 raw_unreliable
    app.state.editor_last_case = {
        'ts': '2026-08-23T12:04:00',
        'task_id': 'tid-0005',
        'raw_text': '直接输出路径的转录',
        'final_text': '直接输出路径的转录',
        'recording_duration': 6.0,
        'audio_src': str(src),
        'source_app': None,
        'mode': 'direct', 'kind': 'direct',
    }
    r4 = svc.mark_last_problem()
    assert r4['ok'] is True
    e = json.loads(svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()[-1])
    assert e['status'] == 'raw_unreliable', e

    # 无效条（0.3s）作为上一条 -> 拒绝（不入库）
    app.state.editor_last_case = {
        'task_id': 'tid-0006', 'raw_text': '', 'recording_duration': 0.3,
        'kind': 'direct', 'mode': 'direct',
    }
    r5 = svc.mark_last_problem()
    assert r5['ok'] is False and r5['reason'] == 'invalid_case', f'实际 {r5}'
    assert len(svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()) == n_lines + 2
    print('  case_mark_last_problem: PASS')


def case_record_error_swallowed(tmp: Path):
    """场景 6：root 不可写时 record 不上抛、mark 返回 write_failed 且可重试"""
    blocker = tmp / 'blocker'
    blocker.write_bytes(b'not-a-dir')  # base_dir 指向一个文件 -> mkdir 必失败
    svc, app = _make_svc(blocker)
    src = tmp / 'fake.mp3'
    src.write_bytes(b'ID3xxxx')
    # 不上抛
    e = svc.record({'task_id': 'tid-z', 'status': 'corrected', 'raw_text': 'r',
                    'final_text': 'f', 'recording_duration': 4.0},
                   audio_src=src)
    assert e['audio_file'] is None
    # mark 落盘失败：返回 write_failed，不置去重标记
    app.state.editor_last_case = {
        'ts': '2026-08-23T12:03:00',
        'task_id': 'tid-0009',
        'raw_text': 'r',
        'recording_duration': 4.0,
        'audio_src': str(src),
        'source_app': None,
        'mode': 'direct', 'kind': 'direct', 'marked': False,
    }
    r = svc.mark_last_problem()
    assert r['ok'] is False and r['reason'] == 'write_failed', f'实际 {r}'
    assert app.state.editor_last_case['marked'] is False, '落盘失败不应置去重标记'
    assert app.error_bus.msgs == [], '落盘失败不应发成功通知'
    print('  case_record_error_swallowed: PASS')


def case_concurrent_mark_is_single_transaction(tmp: Path):
    """两个线程同时标记同一案例时，只允许一次 record 与一次成功返回。"""
    svc, app = _make_svc(tmp)
    app.state.editor_last_case = {
        'ts': '2026-08-24T20:00:00', 'task_id': 'same-task',
        'raw_text': '同一条并发标记', 'recording_duration': 4.0,
        'mode': 'direct', 'kind': 'direct', 'marked': False,
    }
    original_record = svc.record
    first_inside = threading.Event()
    second_inside = threading.Event()
    release_first = threading.Event()
    calls_lock = threading.Lock()
    record_calls = 0

    def _slow_record(case, audio_src=None):
        nonlocal record_calls
        with calls_lock:
            record_calls += 1
            call_no = record_calls
        if call_no == 1:
            first_inside.set()
            assert release_first.wait(2.0), '测试未及时释放首个写入'
        else:
            second_inside.set()
        return original_record(case, audio_src=audio_src)

    svc.record = _slow_record
    results = []
    first = threading.Thread(target=lambda: results.append(svc.mark_last_problem()))
    second = threading.Thread(target=lambda: results.append(svc.mark_last_problem()))
    first.start()
    assert first_inside.wait(2.0), '首线程未进入 record'
    second.start()
    # 旧实现会让第二线程同时进入 record；事务实现会把它挡在外层 RLock。
    second_inside.wait(0.2)
    release_first.set()
    first.join(2.0)
    second.join(2.0)
    assert not first.is_alive() and not second.is_alive(), '并发标记不应死锁'
    assert record_calls == 1, f'同一案例只应写一次，实际 record_calls={record_calls}'
    assert sum(result.get('ok') is True for result in results) == 1, results
    assert sum(result.get('reason') == 'already_marked' for result in results) == 1, results
    lines = svc.jsonl_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1, f'并发标记只允许一行，实际 {len(lines)}'
    print('  case_concurrent_mark_is_single_transaction: PASS')


def case_real_error_bus_delivers_distinct_tasks(tmp: Path):
    """真实 ErrorBus 的 30 秒去重不能吞掉两个不同任务的标记通知。"""
    from core.client.error_bus import ErrorBus

    svc, app = _make_svc(tmp)
    delivered = []
    bus = ErrorBus.__new__(ErrorBus)
    bus._lock = threading.Lock()
    bus._notif_last = {}
    bus._deliver = delivered.append
    app.error_bus = bus

    for task_id in ('notify-a', 'notify-b'):
        app.state.editor_last_case = {
            'ts': f'2026-08-24T20:00:0{len(delivered)}', 'task_id': task_id,
            'raw_text': task_id, 'recording_duration': 4.0,
            'mode': 'direct', 'kind': 'direct', 'marked': False,
        }
        assert svc.mark_last_problem()['ok'] is True

    assert len(delivered) == 2, f'不同 task 的通知都应投递，实际 {delivered}'
    print('  case_real_error_bus_delivers_distinct_tasks: PASS')


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        for sub in ('s1', 's2', 's3', 's4', 's5', 's6', 's7'):
            (tmp / sub).mkdir()
        print('annotation_store 标注落盘测试：')
        case_v2_physical_isolation(tmp / 's1')
        case_record_and_copy(tmp / 's2')
        case_record_no_audio(tmp / 's3')
        case_invalid_filtered(tmp / 's4')
        case_mark_last_problem(tmp / 's5')
        case_record_error_swallowed(tmp)
        case_concurrent_mark_is_single_transaction(tmp / 's6')
        case_real_error_bus_delivers_distinct_tasks(tmp / 's7')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('annotation_store 全部断言通过 ✅')


if __name__ == '__main__':
    main()

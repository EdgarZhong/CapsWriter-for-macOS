#!/usr/bin/env python3
# coding: utf-8
"""编辑框标注 UI 契约的离线回归测试。

本脚本只验证配置与纯函数，不启动 CapsWriterClient、录音、事件循环或 AppKit RunLoop。
这样 macOS 的按键/菜单语义可在提交前快速锁定，避免 UI 回归只能依赖真机手测。
"""
from __future__ import annotations

import os
import sys
import ast
from pathlib import Path

# 直接执行 ``python tools/…`` 时，Python 只会把 tools/ 放入导入路径。
# 显式加入仓库根目录，保证此离线契约测试与 CI/终端执行方式一致。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config_client import ClientConfig as Config
from core.client.output.edit_panel import editor_command
from core.client.shortcut.task import _is_self_target


def _load_pure_function(path: Path, name: str):
    """从 macOS 启动入口离线提取一个纯函数，避免导入 AppKit 或启动客户端。

    `start_client_macos.py` 顶层必须初始化 NSApplication，测试环境可能没有 PyObjC。
    菜单标题函数不依赖该初始化，故只编译其 AST 节点；这样测到的仍是产品源码中的
    同一函数定义，而不是在测试中复制一份实现。
    """
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    namespace: dict[str, object] = {}
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace[name]


mark_item_title = _load_pure_function(PROJECT_ROOT / 'start_client_macos.py', 'mark_item_title')
is_mark_problem_hotkey = _load_pure_function(
    PROJECT_ROOT / 'core/client/shortcut/macos_f18_listener.py',
    'is_mark_problem_hotkey',
)
panel_origin_y = _load_pure_function(
    PROJECT_ROOT / 'core/client/output/edit_panel.py',
    'panel_origin_y',
)


def test_hotkey_and_editor_commands() -> None:
    """按键分类必须以 Shift 状态而非 selector 猜测物理按键。"""
    assert Config.mark_problem_hotkey == '<ctrl>+<alt>+m'
    assert editor_command('insertNewline:', shift_pressed=False) == 'confirm'
    assert editor_command('insertNewline:', shift_pressed=True) == 'newline'
    assert editor_command('insertNewlineIgnoringFieldEditor:', shift_pressed=False) is None
    assert editor_command('cancelOperation:', shift_pressed=False) == 'cancel'
    control, option, command, shift = 0b0001, 0b0010, 0b0100, 0b1000
    assert is_mark_problem_hotkey(
        0x2E, control | option, control, option, command, shift)
    assert not is_mark_problem_hotkey(
        0x2E, control, control, option, command, shift)
    assert not is_mark_problem_hotkey(0x2E, control | option | command,
                                      control, option, command, shift)
    assert not is_mark_problem_hotkey(0x2E, control | option | shift,
                                      control, option, command, shift)
    assert not is_mark_problem_hotkey(
        0x00, control | option, control, option, command, shift)


def test_mark_menu_title() -> None:
    """所有编辑框确认条均归入真值不可靠，不依赖 final 文本是否为空。"""
    assert mark_item_title({'kind': 'editor_confirmed', 'final_text': '已确认'}) == '标记上一条真值不可靠  ⌃⌥M'
    assert mark_item_title({'kind': 'editor_confirmed', 'final_text': ''}) == '标记上一条真值不可靠  ⌃⌥M'
    assert mark_item_title({'kind': 'direct', 'raw_text': '直接输出'}) == '标记上一条转录有误  ⌃⌥M'


def test_panel_grows_downward_from_fixed_top() -> None:
    """面板整体靠上，内容增高时上边缘不动、底边只向下移动。"""
    screen_y, screen_h, top_ratio = 24.0, 1000.0, 0.70
    short_h, tall_h = 62.0, 300.0
    short_y = panel_origin_y(short_h, screen_y, screen_h, top_ratio)
    tall_y = panel_origin_y(tall_h, screen_y, screen_h, top_ratio)
    assert short_y + short_h == tall_y + tall_h
    assert tall_y < short_y
    assert short_y + short_h == screen_y + screen_h * top_ratio


def test_panel_visual_effect_is_clipped_by_independent_rounded_container() -> None:
    """毛玻璃根视图必须再包一层圆角裁剪，防止窗口四角露出矩形背板。"""
    source = (
        PROJECT_ROOT / 'core/client/output/edit_panel.py'
    ).read_text(encoding='utf-8')
    assert 'self.clip_view = NSView.alloc().initWithFrame_' in source
    assert 'self.clip_view.layer().setCornerRadius_(_CORNER_RADIUS)' in source
    assert 'self.clip_view.layer().setMasksToBounds_(True)' in source
    assert 'self.panel.setContentView_(self.clip_view)' in source
    assert 'self.clip_view.addSubview_(self.effect)' in source
    assert 'self.effect.setFrame_(self.clip_view.bounds())' in source


def test_self_target_guard() -> None:
    """自身 PID、bundle 或名称均不得覆盖上一个有效上屏目标，普通应用必须放行。"""
    assert _is_self_target({'pid': os.getpid(), 'bundle_id': 'com.example.editor', 'name': '编辑器'})
    assert _is_self_target({'pid': 1, 'bundle_id': 'com.capswriter.client', 'name': '任意名称'})
    assert _is_self_target({'pid': 1, 'bundle_id': 'com.example.editor', 'name': 'CapsWriter'})
    assert not _is_self_target({'pid': 1, 'bundle_id': 'com.apple.TextEdit', 'name': 'TextEdit'})
    assert not _is_self_target({'pid': 1, 'bundle_id': 'com.capswriter.notes', 'name': 'CapsWriter Notes'})


if __name__ == '__main__':
    test_hotkey_and_editor_commands()
    test_mark_menu_title()
    test_panel_grows_downward_from_fixed_top()
    test_panel_visual_effect_is_clipped_by_independent_rounded_container()
    test_self_target_guard()
    print('PASS: editor UI contract')

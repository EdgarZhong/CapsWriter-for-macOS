# Task 2 报告：锁定编辑面板、热键与菜单契约

## 完成内容

- 将 `ClientConfig.mark_problem_hotkey` 固定为 `<ctrl>+<alt>+m`，避免单独 `⌥M` 被 macOS 输入为特殊字符。
- 在 `edit_panel.py` 抽出无 AppKit 实例依赖的 `editor_command()`：Enter 确认、Shift+Enter 的两个 NSTextView selector 手动换行、Esc 取消、Tab 吞掉；委托只按该分类分流。
- 编辑框改为无边框、自动隐藏垂直滚动条的 `NSScrollView` 承载 `NSTextView`。达到高度上限时，文档视图保持完整内容高度，滚动视口只限制可见高度。
- `task.py` 保留捕获失败及捕获到 CapsWriter 自身时的既有有效 `paste_target`；自身判断使用当前 PID、明确的 CapsWriter bundle/name，避免模糊子串误伤普通应用。
- 在 `start_client_macos.py` 抽出 `mark_item_title()` 纯函数；菜单每次 `menuNeedsUpdate_` 读取实时 `editor_last_case` 并刷新标题。只有 `editor_confirmed + final_text` 显示「真值不可靠」，其他 `editor_canceled`、`direct`（及无 final 的确认条）显示「转录有误」。

## RED / GREEN 证据

RED（补齐项目根导入路径后）：

```text
ImportError: cannot import name 'editor_command' from 'core.client.output.edit_panel'
```

GREEN：

```text
$ python tools/test_editor_ui_contract.py
PASS: editor UI contract

$ python -m py_compile core/client/output/edit_panel.py core/client/shortcut/task.py config_client.py start_client_macos.py tools/test_editor_ui_contract.py
# exit 0

$ git diff --check
# exit 0
```

测试不会启动 CapsWriterClient、录音、事件循环或 AppKit RunLoop。当前执行环境没有 PyObjC，故测试通过 AST 离线编译 `start_client_macos.py` 中同一个 `mark_item_title()` 函数定义，避开启动入口的 AppKit 顶层初始化，同时仍验证产品源码本身。

## 变更文件

- `config_client.py`
- `core/client/output/edit_panel.py`
- `core/client/shortcut/task.py`
- `start_client_macos.py`
- `tools/test_editor_ui_contract.py`
- 本报告

## 长文本滚动判断

修正前的静态证据：`_resize_panel()` 将 `NSTextView` 的自身 frame 高度 clamp 到 `max_content_h - 2 * _MARGIN`，但没有 `NSScrollView`、document view 或垂直滚动条；超过上限的内容没有可达滚动视口，属于裁切风险。因此进行了最小的 `NSScrollView` 包装：无边框、背景透明、仅垂直滚动、自动隐藏滚动条；短文本不增加可见控件。

## 自查与关注点

- 已核对：暂存/提交只包含 Task 2 指定的 4 个实现文件、被忽略但要求提交的定向测试，以及本报告；未包含 `CLAUDE.md`、计划文档或 Task 1 文件。
- `editor_command()` 额外兼容 `insertLineBreak:`，以覆盖 NSTextView 在部分键盘布局中派发的 Shift+Enter selector；产品文案只承诺 Shift+Enter，不承诺 Option+Enter。
- 当前环境缺少 PyObjC，无法对 NSScrollView 的真机可滚动性、毛玻璃和键盘 selector 做运行时验收；需在 macOS 客户端启动后以超长文本完成一次人工复验。

## 修复轮 1/5（`ea032c6` 后 Important findings）

### 修复内容

- 菜单纯函数 `mark_item_title()` 现只按 `editor_last_case.kind == 'editor_confirmed'` 判断「真值不可靠」，不再依赖 `final_text` 是否非空；与 Task 1 的 `final_unreliable` 语义一致。
- `editor_command()` 现显式接收 `shift_pressed`。仅 `insertNewline:` 且 Shift 为真时返回 `newline`；无 Shift 时返回 `confirm`。`insertLineBreak:` 与 `insertNewlineIgnoringFieldEditor:` 不再被 selector 本身误判为 Shift+Enter，因为标准 AppKit 键绑定可将它们用于 Control/Option 组合。
- `textView_doCommandBySelector_` 从 `NSEvent.modifierFlags()` 读取 `NSEventModifierFlagShift` 后传给纯函数，保持运行时路径与离线测试的同一分类逻辑。
- 定向测试补充了 `CapsWriter Notes` / `com.capswriter.notes` 的自身目标守卫负例。

### RED / GREEN 证据与完整输出

RED（先更新测试，尚未更新纯函数签名）：

```text
$ python tools/test_editor_ui_contract.py
Traceback (most recent call last):
  File "/Users/edgar/programs/CapsWriter-Offline/tools/test_editor_ui_contract.py", line 73, in <module>
    test_hotkey_and_editor_commands()
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/Users/edgar/programs/CapsWriter-Offline/tools/test_editor_ui_contract.py", line 49, in test_hotkey_and_editor_commands
    assert editor_command('insertNewline:', shift_pressed=False) == 'confirm'
           ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: editor_command() got an unexpected keyword argument 'shift_pressed'
```

GREEN：

```text
$ python tools/test_editor_ui_contract.py
PASS: editor UI contract

$ python -m py_compile core/client/output/edit_panel.py core/client/shortcut/task.py config_client.py start_client_macos.py tools/test_editor_ui_contract.py
# exit 0（无输出）

$ git diff --check
# exit 0（无输出）
```

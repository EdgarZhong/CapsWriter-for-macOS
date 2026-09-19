# Task 1：统一无效条判定并锁定结果流语义

## 结论

完成。无效条的唯一判定函数已由结果入口和标注落盘层共同复用；时长未知且空文本不再被误判为无效条。结果流隔离回归已覆盖无效条、direct、Enter 与 Esc 的要求顺序和副作用。

## 实现

- `core/client/output/annotation_store.py`
  - 导出 `is_invalid_annotation_case(raw_text, recording_duration)`，严格执行：时长 `<0.5s`，或时长已知且 `<2s` 且空文本才无效；时长未知一律不判无效。
  - `record()` 与 `mark_last_problem()` 都通过该函数兜底，防止上游漏网无效条进入数据集。
- `core/client/output/result_processor.py`
  - 删除入口处重复布尔表达式，改用唯一函数，并传入原始转录 `original_text` 和 trace 推导出的时长。
  - 保留无效条通知正文精确短语「录音时间过短或为空」，并维持 `invalid_case_{task_id}` 通知 key。
- `tools/test_editor_annotation.py`
  - 红灯边界改为“时长未知＋空文本不跳过”；保留 `<0.5s`、`<2s 且空`、`>2s 空`、`0.70s 非空`四个边界。
- `tools/test_editor_result_flow.py`
  - 新增 fake app/state/annotation 与 mock 的隔离回归，覆盖简报指定的六类结果流。
  - 测试环境没有 `pyclip`、`sounddevice` 时，仅在测试脚本导入前提供最小替身；测试不启动真实客户端、不访问真实剪贴板、不打开音频设备。

## RED 证据

命令：

```text
python tools/test_editor_annotation.py
```

修改实现前退出码为 `1`，关键输出：

```text
AssertionError: 时长未知不能仅凭空文本判成无效条
```

说明旧私有判定会将 `recording_duration=None` 且空文本错误跳过。

## GREEN 证据

命令：

```text
python tools/test_editor_annotation.py
python tools/test_editor_result_flow.py
python -m py_compile core/client/output/annotation_store.py core/client/output/result_processor.py tools/test_editor_annotation.py tools/test_editor_result_flow.py
git diff --check
```

结果：全部退出码 `0`。

```text
annotation_store 标注落盘测试：
  case_record_and_copy: PASS
  case_record_no_audio: PASS
  case_invalid_filtered: PASS
  case_mark_last_problem: PASS
  case_record_error_swallowed: PASS
annotation_store 全部断言通过 ✅

  case_invalid_short_text_direct_and_keeps_last: PASS
  case_invalid_empty_keeps_last: PASS
  case_unknown_empty_enters_editor: PASS
  case_direct_registers_after_emit: PASS
  case_editor_confirmed_order: PASS
  case_editor_canceled_clipboard_only: PASS
editor 结果流全部断言通过 ✅
```

`case_record_error_swallowed` 会按设计输出两条“标注落盘失败”日志，以验证不可写路径被吞掉并允许重试；该测试断言通过，不是测试失败。

## 变更文件

- `core/client/output/annotation_store.py`
- `core/client/output/result_processor.py`
- `tools/test_editor_annotation.py`
- `tools/test_editor_result_flow.py`（新增）
- `.superpowers/sdd/2026-08-24-editor-annotation-semantics-redefinition/task-1-report.md`（本报告）

## 自查

- 判定条件只包含 `<0.5s` 与“时长已知且 `<2s` 且空”；`None + 空` 通过存储与结果入口两条测试覆盖。
- 无效条仍回落到直接输出路径，且不会覆盖旧 `editor_last_case`；连续两条短录音的不同通知 key 已测试。
- 有效 direct 在 `_emit_text` 完成后才登记；Enter 的顺序为 `corrected` → `editor_confirmed` → 恢复焦点 → `paste=True` 上屏；Esc 不写标注、不上屏、非空只写剪贴板。
- 未修改 Task 2 文件：`edit_panel.py`、`task.py`、`config_client.py`、`start_client_macos.py` 均保持其既有工作区差异。
- `git diff --check` 无输出。

## 关注点

- 新结果流脚本为隔离单元回归，使用最小依赖替身绕过当前环境缺失的可选 `pyclip` 和 `sounddevice`；真实 macOS 面板、系统剪贴板和音频设备的用户级验收仍由 Task 3 执行。

---

## 修复轮 1（评审 finding 修复）

### 修复内容

- `core/client/output/annotation_store.py`
  - `editor_confirmed` 的标记 status 现在只由 `kind == 'editor_confirmed'` 决定，不再要求 `final_text` 为真值。
  - 用户清空编辑框后 Enter 时，后续标记会正确写入 `final_unreliable`，保存空 `final_text`；通知摘要仍按“final 优先、为空则 raw”回退。
- `core/client/output/result_processor.py`
  - direct 路径只有在后处理后的 `text` 非空时才登记 `editor_last_case`。
  - 这与 `TextOutput.output()` 对空文本直接 return 的行为一致，避免“未写剪贴板”却覆盖上一条可标记案例。

### 新增回归

- `tools/test_editor_annotation.py`
  - 新增 `editor_confirmed + final_text=''`：断言落盘 `final_unreliable`、保留空 final、通知从 raw 摘录。
- `tools/test_editor_result_flow.py`
  - 新增后处理为空的 direct：断言 `_emit_text('', ...)` 后旧 `editor_last_case` 保持不变且不发生新的 last_case 登记。

### RED 证据

命令：

```text
python tools/test_editor_annotation.py
python tools/test_editor_result_flow.py
```

修改前完整输出（两条命令均退出码 `1`）：

```text
annotation_store 标注落盘测试：
  case_record_and_copy: PASS
  case_record_no_audio: PASS
  case_invalid_filtered: PASS
Traceback (most recent call last):
  File "tools/test_editor_annotation.py", line 289, in <module>
    main()
  File "tools/test_editor_annotation.py", line 281, in main
    case_mark_last_problem(tmp / 's4')
  File "tools/test_editor_annotation.py", line 188, in case_mark_last_problem
    assert e['status'] == 'final_unreliable' and e['final_text'] == '', e
AssertionError: {'ts': '2026-08-23T12:02:30', 'task_id': 'tid-0003-empty-final', 'status': 'raw_unreliable', 'raw_text': '用户清空前的原始转录', 'final_text': None, 'recording_duration': 5.0, 'source_app': None, 'mode': 'editor', 'kind': 'editor_confirmed', 'audio_file': 'audio/20260823T120230_tid-0003.mp3'}

  case_invalid_short_text_direct_and_keeps_last: PASS
  case_invalid_empty_keeps_last: PASS
  case_unknown_empty_enters_editor: PASS
  case_direct_registers_after_emit: PASS
Traceback (most recent call last):
  File "tools/test_editor_result_flow.py", line 299, in <module>
    asyncio.run(main())
  File "tools/test_editor_result_flow.py", line 292, in main
    await case_direct_empty_after_processing_keeps_last()
  File "tools/test_editor_result_flow.py", line 243, in case_direct_empty_after_processing_keeps_last
    assert app.state.editor_last_case is old_case, '未写剪贴板的空结果不得覆盖旧 editor_last_case'
AssertionError: 未写剪贴板的空结果不得覆盖旧 editor_last_case
```

### GREEN 证据

命令：

```text
python tools/test_editor_annotation.py
python tools/test_editor_result_flow.py
python -m py_compile core/client/output/annotation_store.py core/client/output/result_processor.py tools/test_editor_annotation.py tools/test_editor_result_flow.py
```

完整输出（全部退出码 `0`；`py_compile` 成功时无 stdout）：

```text
annotation_store 标注落盘测试：
  case_record_and_copy: PASS
  case_record_no_audio: PASS
  case_invalid_filtered: PASS
  case_mark_last_problem: PASS
[08/24/26 20:53:21] ERROR 标注落盘失败（不影响正常输出流程）: [Errno 20] Not a directory: '.../blocker/evals/manual_cases/audio'
[08/24/26 20:53:21] ERROR 标注落盘失败（不影响正常输出流程）: [Errno 20] Not a directory: '.../blocker/evals/manual_cases/audio'
  case_record_error_swallowed: PASS
annotation_store 全部断言通过 ✅

    转录时延：1.00s
    识别结果：嗯
    录音时间过短或为空，本条不计入标注系统
    转录时延：1.00s
    识别结果：啊
    录音时间过短或为空，本条不计入标注系统
  case_invalid_short_text_direct_and_keeps_last: PASS
    转录时延：1.00s
    识别结果：
    录音时间过短或为空，本条不计入标注系统
  case_invalid_empty_keeps_last: PASS
    转录时延：1.00s
    识别结果：
  case_unknown_empty_enters_editor: PASS
    转录时延：1.00s
    识别结果：直接输出
  case_direct_registers_after_emit: PASS
    转录时延：1.00s
    识别结果：
  case_direct_empty_after_processing_keeps_last: PASS
  case_editor_confirmed_order: PASS
  case_editor_canceled_clipboard_only: PASS
editor 结果流全部断言通过 ✅

python -m py_compile ...
（无 stdout，退出码 0）
```

# Task 3 报告：标注数据 v2 物理隔离与记录版本

## 结论

新标注现在只写入 `<base>/evals/manual_cases/v2/cases.jsonl` 和 `v2/audio/`，每条 entry 由 `AnnotationService` 固定写入 `annotation_version: 2`。旧 `manual_cases/cases.jsonl` 与旧音频不迁移、不改写。

## TDD 记录

### RED

命令：

```text
.venv/bin/python tools/test_editor_annotation.py
```

关键输出（修改测试、尚未实现 v2 时）：

```text
annotation_store 标注落盘测试：
Traceback (most recent call last):
  ...
    assert svc.root == tmp / 'evals' / 'manual_cases' / 'v2'
AssertionError
退出码：1
```

失败原因符合预期：当时 `AnnotationService.root` 仍指向旧 `evals/manual_cases` 根目录。

### GREEN

命令：

```text
.venv/bin/python tools/test_editor_annotation.py
```

关键输出：

```text
annotation_store 标注落盘测试：
  case_v2_physical_isolation: PASS
  case_record_and_copy: PASS
  case_record_no_audio: PASS
  case_invalid_filtered: PASS
  case_mark_last_problem: PASS
  case_record_error_swallowed: PASS
annotation_store 全部断言通过
退出码：0
```

`case_record_error_swallowed` 会输出两条“标注落盘失败”日志，来自该既有测试刻意以普通文件充当 `base_dir`、验证落盘异常被吞没且可重试的路径；该场景断言通过，不是回归失败。

## 旧文件不变证据

`case_v2_physical_isolation` 只在 `tempfile` 中构造假的旧 `evals/manual_cases/cases.jsonl`，预先保存字节串 `old_bytes`。在调用新版 `record()` 后，测试断言：

```python
assert old_jsonl.read_bytes() == old_bytes
```

同一测试还在全新临时根目录断言旧根目录 JSONL 不会被创建，并验证 `svc.root` 是 `manual_cases/v2`、`annotation_version == 2`、`audio_file` 以 `audio/` 开头。测试没有读取、打印或修改真实个人 `cases.jsonl` 或音频内容。

## 定向验证

```text
.venv/bin/python tools/test_editor_annotation.py
# exit 0；6 个场景全部 PASS

.venv/bin/python tools/test_editor_result_flow.py
# exit 0；8 个编辑结果流场景全部 PASS

.venv/bin/python -m py_compile core/client/output/annotation_store.py tools/test_editor_annotation.py
# exit 0

git diff --check
# exit 0；无空白错误
```

## 变更文件

- `core/client/output/annotation_store.py`：新增 `ANNOTATION_VERSION = 2`；固定 v2 根目录；统一 entry 强制写版本字段。
- `tools/test_editor_annotation.py`：新增临时目录 v2 隔离与假旧文件逐字节不变测试。
- `evals/README.md`：明确 v1/v2 目录、可信度和默认评测读取规则。
- `.superpowers/sdd/2026-08-24-editor-annotation-semantics-redefinition/task-3-report.md`：本报告。

## 自查与关注点

- 三种状态 `corrected`、`final_unreliable`、`raw_unreliable` 均通过同一 `record()` 规范化入口，因此固定携带版本字段。
- 本任务未读取、迁移、改写或展示真实 v1 个人语料和音频；旧数据兼容仅在文档中声明为显式分析行为。
- 本任务未实现评测驱动的读取筛选逻辑；当前仓库尚无相关驱动，README 已明确后续评测默认使用 v2。

## 修复轮 1：v2 个人数据 Git 忽略与旧音频保护

### 修复内容

- `.gitignore` 明确忽略 `evals/manual_cases/v2/cases.jsonl` 与整个 `evals/manual_cases/v2/audio/`，后者不依赖固定扩展名，覆盖所有音频格式。
- `case_v2_physical_isolation` 在临时目录构造假旧 `manual_cases/audio/legacy-sentinel.flac`，保存文件清单及每个文件的字节快照；调用新版 `record()` 后断言旧 JSONL、旧 audio 清单、旧 audio 哨兵字节均不变，并确认新音频存在于 `v2/audio/`。
- 同一场景向调用方传入伪造的 `annotation_version: 1`，断言存储服务仍强制写入 2。

### 完整验证命令与输出

```text
.venv/bin/python tools/test_editor_annotation.py
# exit 0
annotation_store 标注落盘测试：
  case_v2_physical_isolation: PASS
  case_record_and_copy: PASS
  case_record_no_audio: PASS
  case_invalid_filtered: PASS
  case_mark_last_problem: PASS
  case_record_error_swallowed: PASS
annotation_store 全部断言通过
```

其中 `case_record_error_swallowed` 的两条预期“标注落盘失败”日志来自故意传入普通文件作为 `base_dir` 的异常吞没测试，不代表验证失败。

```text
.venv/bin/python tools/test_editor_result_flow.py
# exit 0
case_invalid_short_text_direct_and_keeps_last: PASS
case_invalid_empty_keeps_last: PASS
case_unknown_empty_enters_editor: PASS
case_direct_registers_after_emit: PASS
case_direct_empty_after_processing_keeps_last: PASS
case_editor_confirmed_order: PASS
case_editor_canceled_clipboard_only: PASS
editor 结果流全部断言通过

.venv/bin/python -m py_compile core/client/output/annotation_store.py tools/test_editor_annotation.py
# exit 0

git diff --check
# exit 0

git check-ignore -v evals/manual_cases/v2/cases.jsonl evals/manual_cases/v2/audio/example.flac
.gitignore:226:evals/manual_cases/v2/cases.jsonl evals/manual_cases/v2/cases.jsonl
.gitignore:228:evals/manual_cases/v2/audio/ evals/manual_cases/v2/audio/example.flac
# exit 0
```

本轮所有旧数据证据只来自 `tempfile` 伪造的字节串与音频哨兵；未读取、打印、修改真实旧个人数据。

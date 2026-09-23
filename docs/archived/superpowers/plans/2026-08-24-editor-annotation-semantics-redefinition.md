# 编辑框标注 8 月 24 日口径重定义续接计划

> **当前执行方式（2026-08-25 用户确认）：** 不再新派子代理；剩余纠偏、验证与收尾全部由主会话按本计划逐项完成。复选框用于记录真实完成状态。

**Goal:** 在保留 8 月 23 日已完成编辑框功能的基础上，使无效条、Enter/Esc、「上一条」标记、菜单文案与面板交互严格符合 2026-08-24 用户重定义口径，并完成真实用户路径验收。

**Architecture:** 标注域以单一纯函数判定无效条，`ResultProcessor` 只负责在结果入口应用该判定并保持既有输出链路；`AnnotationService` 复用同一判定作落盘兜底。“数据集落盘”与“可标记上一条指针”严格分离：指针只允许 `editor_confirmed` / `direct` 两种，分别在 Enter 确认关闭、direct 成功写入剪贴板时推进；Esc、无效条与框内待编辑状态完全不参与指针。AppKit 面板只负责采集确认/放弃和文本，不直接承担标注或输出副作用。

**Tech Stack:** Python 3.13、asyncio、PyObjC/AppKit、现有脚本式回归测试。

**Spec:** `CLAUDE.md`「编辑框标注行为口径（2026-08-24 用户重新声明）」；本计划是 8 月 23 日计划完成后的续接，不重做其 Task 1–10。

## Global Constraints

- 无效条只命中：录音时长 `<0.5s`；或录音时长已知且 `<2s` 且转录为空。时长未知不得仅因文本为空而判无效。
- 无效条闸门只管标注域：每条必通知「录音时间过短或为空」，不开编辑框、不更新 `editor_last_case`，剪贴板/上屏/音频/日记链路保持既有行为。
- Enter 确认关闭时立即登记 `editor_confirmed`，自动保存 `corrected`，恢复目标应用并上屏；慢 I/O 或单项失败不得撤回已经越过的用户边界。
- Esc 完全退出标注域：不写数据集、不创建任何取消类型、不改变既有指针；非空面板文本只写剪贴板、不自动上屏，清空后不覆盖剪贴板，两者都恢复目标应用。
- 非编辑框路径默认不写任何数据集记录；只有成功写入剪贴板后才登记 `direct`。
- 标记 `editor_confirmed` 写 `final_unreliable`；标记 `direct` 写 raw-only `raw_unreliable`；通知包含优先 final、否则 raw 的文本摘录。
- 快捷键固定为 `<ctrl>+<alt>+m`，由 macOS active event tap 吞掉 keyDown/keyUp 后异步执行，禁止产生系统错误音或特殊字符；菜单标题与动作只随当前指针类型切换，无指针时禁用。
- 编辑框只有文本编辑区域，按宽度自动换行；Enter 确认，Shift+Enter 插入换行，Esc 放弃；高度随内容增长并有屏幕比例上限，超限可滚动；窗口水平居中、纵向靠上，顶部固定，高度只向下伸缩。
- 所有新增或修改代码写详细中文注释；不引入新依赖；不删除文件。
- 新系统只写 `evals/manual_cases/v2/cases.jsonl` 与 `evals/manual_cases/v2/audio/`；每条固定 `annotation_version: 2`。旧目录不迁移、不改写，缺字段按 v1；后续评测默认选 v2。

---

### Task 1: 统一无效条判定并锁定结果流语义

**Files:**
- Modify: `core/client/output/annotation_store.py`
- Modify: `core/client/output/result_processor.py`
- Modify: `tools/test_editor_annotation.py`
- Create: `tools/test_editor_result_flow.py`

**Interfaces:**
- Produces: `is_invalid_annotation_case(raw_text: str, recording_duration: Optional[float]) -> bool`
- Consumes: `ResultProcessor._handle_message()` 取得的 `original_text` 与 trace 时长。

- [x] **Step 1: 先把错误边界改成红灯测试**

  将 `tools/test_editor_annotation.py` 中“时长未知且空文本无效”的断言改为：未知时长空文本不跳过；保留 `<0.5s`、`<2s 且空`、`>2s 空`、`0.70s 非空`四个边界。

  ```python
  unknown = svc.record({'task_id': 'unknown', 'raw_text': ''})
  assert not unknown.get('skipped'), '时长未知不能仅凭空文本判成无效条'
  ```

- [x] **Step 2: 运行测试确认当前实现失败**

  Run: `python tools/test_editor_annotation.py`

  Expected: FAIL，指出时长未知空文本被错误跳过。

- [x] **Step 3: 建立唯一判定函数并让两层复用**

  在 `annotation_store.py` 实现并导出：

  ```python
  def is_invalid_annotation_case(raw_text: str,
                                 recording_duration: Optional[float]) -> bool:
      """严格按 2026-08-24 口径判断标注域无效条。"""
      if recording_duration is None:
          return False
      duration = float(recording_duration)
      return duration < 0.5 or (duration < 2.0 and not (raw_text or '').strip())
  ```

  `AnnotationService.record()` 与 `mark_last_problem()` 通过该函数兜底；`result_processor.py` 删除重复布尔表达式，使用相同函数。无效条通知 key 继续包含 `task_id`，通知正文至少包含精确短语「录音时间过短或为空」。

- [x] **Step 4: 新增结果流隔离回归**

  `tools/test_editor_result_flow.py` 用 fake app/state/output/annotation 和 `unittest.mock` 覆盖：

  1. `0.3s + 非空`：不打开面板、每条使用不同通知 key、仍走直接输出、不改旧 `editor_last_case`；
  2. `1.2s + 空`：不打开面板、不改旧 `editor_last_case`；
  3. `duration=None + 空`：不触发无效通知，允许进入编辑框路径；
  4. 有效 direct：`_emit_text` 完成后登记 `kind=direct`；
  5. `_editor_confirmed`：确认回调关闭面板时立即登记 `editor_confirmed`；随后写 `corrected`、恢复焦点并 `_emit_text(..., paste=True)`，单项 I/O 失败不得撤回指针；
  6. `_editor_canceled`：不调用 `annotation.record`，非空文本调用 `safe_copy`，不调用 `_emit_text`，恢复焦点，并断言原有 `editor_last_case` 完全不变。

- [x] **Step 5: 定向验证并提交**

  Run: `python tools/test_editor_annotation.py`

  Run: `python tools/test_editor_result_flow.py`

  Run: `python -m py_compile core/client/output/annotation_store.py core/client/output/result_processor.py tools/test_editor_annotation.py tools/test_editor_result_flow.py`

  Commit: `fix(macos/editor): 对齐 8 月 24 日标注结果流口径`

---

### Task 2: 锁定编辑面板、热键与菜单契约

**Files:**
- Modify: `core/client/output/edit_panel.py`
- Modify: `core/client/shortcut/task.py`
- Modify: `config_client.py`
- Modify: `start_client_macos.py`
- Create: `tools/test_editor_ui_contract.py`

**Interfaces:**
- Consumes: `editor_last_case.kind` 的 `editor_confirmed/direct` 两值；Esc 不产生 kind。
- Produces: 可静态验证的按键命令分类与菜单标题函数；AppKit 运行时仍由现有 `EditorPanelController` 接线。

- [x] **Step 1: 为 UI 契约写红灯测试**

  `tools/test_editor_ui_contract.py` 至少断言：

  ```python
  assert Config.mark_problem_hotkey == '<ctrl>+<alt>+m'
  assert editor_command('insertNewline:', shift_pressed=False) == 'confirm'
  assert editor_command('insertNewline:', shift_pressed=True) == 'newline'
  assert editor_command('cancelOperation:', shift_pressed=False) == 'cancel'
  ```

  同时验证菜单标题：`editor_confirmed` 为「真值不可靠」，`direct` 为「转录有误」，无指针时禁用；验证 self-target 守卫可识别当前 PID、CapsWriter bundle/name，且不会误判普通应用。

- [x] **Step 2: 抽取纯函数并复用**

  在 `edit_panel.py` 增加不依赖 AppKit 实例的命令分类函数，委托方法只按分类调用 `_confirm()`、插入 `\n`、`_cancel()` 或吞掉 Tab。注释和模块说明只承诺 Shift+Enter，不把 Option+Enter 写成正式产品口径。

  用无边框、自动隐藏滚动条的 `NSScrollView` 承载文本视图，保证达到高度上限后仍可编辑全部内容。面板的纵向靠上与固定顶部伸缩属于真机验收后的新增纠偏，见 Task 8。

- [x] **Step 3: 验证目标应用与菜单动态语义**

  保持 `task.py` 捕获到 CapsWriter 自身或捕获失败时不覆盖上一个有效 `paste_target`；菜单每次打开时读取实时 `editor_last_case` 刷新标题。测试不得导入并启动完整客户端。

- [x] **Step 4: 定向验证并提交**

  Run: `python tools/test_editor_ui_contract.py`

  Run: `python -m py_compile core/client/output/edit_panel.py core/client/shortcut/task.py config_client.py start_client_macos.py tools/test_editor_ui_contract.py`

  Commit: `fix(macos/editor): 固化编辑面板与标记入口契约`

---

### Task 3: 标注数据 v2 物理隔离与记录版本

**Files:**
- Modify: `core/client/output/annotation_store.py`
- Modify: `tools/test_editor_annotation.py`
- Modify: `evals/README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ANNOTATION_VERSION = 2`；新版根目录 `<base>/evals/manual_cases/v2`。
- Consumes: `AnnotationService.record()` 与 `mark_last_problem()` 的既有统一落盘入口。

- [x] **Step 1: 先写失败测试**

  在隔离临时目录中断言：

  ```python
  assert svc.root == tmp / 'evals' / 'manual_cases' / 'v2'
  assert entry['annotation_version'] == 2
  assert not (tmp / 'evals' / 'manual_cases' / 'cases.jsonl').exists()
  assert entry['audio_file'].startswith('audio/')
  ```

  同时创建一份假的旧 `evals/manual_cases/cases.jsonl`，调用新版 `record()` 后断言其内容逐字节不变。
  在假的旧 `manual_cases/audio/` 放置哨兵文件，调用新版 `record()` 后断言旧目录文件清单与哨兵字节均不变。

- [x] **Step 2: 运行测试确认当前实现失败**

  Run: `.venv/bin/python tools/test_editor_annotation.py`

  Expected: FAIL，指出根目录仍是旧 `manual_cases` 或缺少 `annotation_version`。

- [x] **Step 3: 最小实现 v2**

  在 `annotation_store.py` 定义：

  ```python
  ANNOTATION_VERSION = 2
  ```

  `AnnotationService.root` 固定为 `Path(app.base_dir) / 'evals' / 'manual_cases' / 'v2'`；规范化 entry 固定写 `'annotation_version': ANNOTATION_VERSION`，不允许调用方覆盖。`audio_file` 继续相对 v2 根记录为 `audio/<name>`，所有 corrected/final_unreliable/raw_unreliable 都经同一 `record()` 自动带版本。

- [x] **Step 4: 同步稳定评测目录约定**

  在 `evals/README.md` 说明：旧根目录无版本字段的数据视作 v1、可信度较低；v2 是 2026-08-24 新口径，物理隔离且每条带版本；评测默认读取 v2，只有显式兼容分析才读取 v1。不得写入或展示个人语料内容。

  在 `.gitignore` 明确忽略 `evals/manual_cases/v2/cases.jsonl` 与整个 `evals/manual_cases/v2/audio/`，确保新版真实文本和任意音频格式都不会出现在 Git 待提交清单。

- [x] **Step 5: 定向验证并提交**

  Run: `.venv/bin/python tools/test_editor_annotation.py`

  Run: `.venv/bin/python tools/test_editor_result_flow.py`

  Run: `.venv/bin/python -m py_compile core/client/output/annotation_store.py tools/test_editor_annotation.py`

  Run: `git diff --check`

  Commit: `feat(evals): 隔离 v2 高可信标注数据`

---

### Task 4: 输出成功状态逐层回传

**Files:**
- Modify: `core/client/clipboard/clipboard.py`
- Modify: `core/client/output/text_output.py`
- Modify: `core/client/output/result_processor.py`
- Modify: `tools/test_editor_result_flow.py`

**Interfaces:** `paste_text/TextOutput.output/ResultProcessor._emit_text -> bool`；macOS 以 `safe_copy` 成功为 True 边界。

- [x] 先补 RED：`safe_copy=False` 时不发送粘贴；`_emit_text=False` 时不更新 output state/UDP；direct 不登记上一条。
- [x] 最小实现 bool 回传；复制成功但 Cmd+V 权限失败仍返回 True，因为文本已在剪贴板。
- [x] 运行 `test_editor_result_flow.py`、`py_compile`、`git diff --check`，立即提交 `fix(macos/editor): 回传文本输出成功状态`。

---

### Task 5: 上屏目标按 trace 隔离

**Files:**
- Modify: `core/client/state.py`
- Modify: `core/client/shortcut/task.py`
- Modify: `core/client/output/result_processor.py`
- Modify: `tools/test_editor_result_flow.py`

**Interfaces:** `ClientState.start_recording(..., paste_target=None)` 将目标快照写入 trace context。

- [x] 先补 RED：A/B 两条在途录音各自保存目标；A 结果返回时不能读取后来 B 的全局目标。
- [x] `ShortcutTask.launch()` 传本轮快照；ResultProcessor 优先读 task trace，只有无 trace 来源才回退全局；确认时用 `case['source_app']`。
- [x] 运行结果流测试与三文件编译，立即提交 `fix(macos/editor): 按任务隔离上屏目标`。

---

### Task 6: 在用户边界立即发布上一条

**Files:**
- Modify: `core/client/output/result_processor.py`
- Modify: `tools/test_editor_result_flow.py`

- [x] 先补 RED：confirm 回调派发协程前已发布；cancel 永不推进上一条；direct 输出 True 后、归档前发布；归档/日记/标注异常不撤回、不阻断上屏/剪贴板。
- [x] confirm provisional case 立即发布；Esc 保留此前上一条并恢复目标焦点；I/O 分项容错；归档后仅在 task_id 仍匹配时回填 `audio_src`。
- [x] 运行结果流测试与编译，立即提交 `fix(macos/editor): 立即发布可标记上一条`。

---

### Task 7: 并发标记事务与通知去重

**Files:**
- Modify: `core/client/output/annotation_store.py`
- Modify: `tools/test_editor_annotation.py`

- [x] 先补确定性 RED：两个线程标记同一案例只允许一条写入；真实 ErrorBus 在 30 秒内标记两个不同 task 必须投递两条摘录通知。
- [x] 用 `RLock` 覆盖 check→record→marked；`record()` 返回本次 `write_ok` 且不污染 JSONL；通知 key 带 task_id/ts。
- [x] 运行标注/结果流测试与编译，立即提交 `fix(macos/editor): 串行化标记事务与通知`。

---

### Task 8: 修复真机验收暴露的最终口径偏差

**Files:**
- Modify: `core/client/output/result_processor.py`
- Modify: `core/client/shortcut/macos_f18_listener.py`
- Modify: `core/client/shortcut/macos_caps_f18.py`
- Modify: `core/client/app.py`
- Modify: `core/client/output/edit_panel.py`
- Modify: `start_client_macos.py`
- Modify: `tools/test_editor_result_flow.py`
- Modify: `tools/test_editor_ui_contract.py`

- [x] Esc 取消回调不再发布任何新指针；非空文本只复制，空文本不覆盖剪贴板；两者恢复原目标应用，并以回归测试锁定“Esc 前指针完全不变”。
- [x] 将 ⌃⌥M 接入现有 macOS active event tap，吞掉 keyDown/keyUp，并把标记动作送到 worker 队列异步执行；原生桥接存在时不再重复注册 pynput 热键。
- [x] 面板改为纵向靠上，内容高度变化时保持顶部位置不变，仅向下增长或从底部缩回；保留最大高度与滚动能力。
- [x] 运行 fresh 定向测试与编译，主会话复核差异后提交本轮纠偏（`5249253`）。

---

### Task 9: 文档同步与自主用户级验收

**Files:**
- Modify: `CLAUDE.md`
- Modify: `readme.md`（仅当稳定使用入口发生变化）
- Create: `docs/autonomous-runs/20260824-HHmm-编辑框标注口径重定义.md`

**Interfaces:**
- Consumes: Task 1–8 的最终行为和测试证据。
- Produces: 全部用户行为路径、预期、实测结果、证据和遗留风险。

- [x] **Step 1: 验收前先写完整路径表**

  至少包含：编辑框 Enter 原样确认、编辑后确认、Shift+Enter 多行、Esc 非空、Esc 清空、Esc 后标记仍命中此前合法条、面板打开时再次长按 Caps、direct 默认不入库、两类上一条标记、连续无效条、0.50/2.00 秒边界、通知摘录、菜单动态标题、⌃⌥M 吞键无错误音、目标应用退出/捕获失败、长文本高度上限与顶部固定向下伸缩。

- [x] **Step 2: 运行完整自动验证**

  Run: `python tools/test_stream_stop_leak.py`

  Run: `python tools/test_editor_annotation.py`

  Run: `python tools/test_editor_result_flow.py`

  Run: `python tools/test_editor_ui_contract.py`

  Run: `python -m compileall -q core config_client.py start_client_macos.py`

- [ ] **Step 3: 重启真实客户端并逐条执行用户路径**

  重启前在会话中明确告知用户 CapsWriter 会短暂不可用；使用真实菜单栏、Caps 长按、编辑面板、系统剪贴板和目标应用逐项验收。任何失败都回到 Task 1 或 Task 2 修复并重新完整验收。

  进度：客户端已真实重启并确认新 PID；用户已接管并通过 Enter 标记、Esc 后标记、direct 标记及 ⌃⌥M 核心路径。Shift+Enter、长文本滚动/固定顶部伸缩等其余路径仍待执行，因此本步骤保持未完成。

- [ ] **Step 4: 回写结果并提交**

  `CLAUDE.md` 只写动态状态、裁定与验收结论；`readme.md` 仅在稳定入口变化时更新。提交前检查 `git diff --check` 和 `git status --short`，不得混入 `evals/manual_cases` 个人数据。

  Commit: `docs(macos/editor): 记录 8 月 24 日口径验收结果`

---

### Task 10: 优化 Enter 到上屏的用户可见时延

**Files:**
- Modify: `core/client/output/result_processor.py`
- Modify: `tools/test_editor_result_flow.py`
- Modify: `CLAUDE.md`

- [x] 先补 RED：确认回调仍同步发布 `editor_confirmed`；异步处理顺序必须为恢复目标应用、`_emit_text(..., paste=True)`、音频/日记归档、`corrected` v2 记录。
- [x] 最小调整 `_editor_confirmed` 顺序；上屏失败仍继续持久化，归档/标注失败不得反向影响已经完成的上屏。
- [x] 第一轮仅后置持久化后，用户真机仍感到约半秒延迟；分段计时定位到固定 250ms 焦点等待、50ms 剪贴板等待与约 175ms `osascript` 启动成本。
- [x] 第二轮增加 Quartz Cmd+V 快速路径，移除 macOS 无用的旧剪贴板读取和固定 50ms sleep；焦点恢复改为激活即返回、最多 80ms 条件轮询，AppleScript 仅作异常兜底。
- [x] 运行结果流测试、UI 契约测试、编译与差异检查；提交 `d5651c2` 并重启。用户真机确认即时上屏、焦点中途切换、面板失焦后重新确认三条路径均符合预期，并授权推送。

## Self-Review 结论

- Spec 覆盖：无效条、Enter/Esc、两种指针类型、三种落盘 status、direct 默认不入库、通知摘录、原生吞键热键、菜单动态文案、面板 UI 与固定顶部伸缩均有对应任务。
- 冲突检查：Task 1 与 Task 2 不共享业务实现文件；Task 3 只消费前两项结果。
- 占位扫描：无 TBD/TODO/“类似前文”；所有测试命令、阈值、状态值和快捷键均为精确值。
- 恢复原则：8 月 23 日旧计划仅作历史账本；现行口径只认 `CLAUDE.md` 本轮决策、本计划与对应自主验收记录。剩余工作由主会话完成，不再新派子代理。

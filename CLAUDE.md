# CapsWriter-Offline 当前阶段同步

## 2026-09-19：本轮修复提交与推送（收尾，工作区已清空）

- 用户要求把两端 bug 修复提交推送并清空工作区；按主题拆 5 个提交：服务端调度、客户端麦克风、子模块指针、编辑框圆角、文档与忽略规则。分支 `mac-dev` 已推送，工作区无残留改动。
- 提交：`633814b`（服务端调度）、`29783ac`（客户端麦克风）、`72b7aa5`（子模块指针 25551b0→4247e58）、`92a7d46`（编辑框圆角）、本轮文档提交；此前未推送的 `be84e5e`（取证脚本）一并推送。
- `diagnogs/` 取证数据（约 3.6G，含 9313 条 Windows 对照转写）按用户决定不入库；`.gitignore` 改为只保留 `serve_transcribe.py` / `serve_transcribe_resume.py` 两个脚本入库，其余全部忽略。
- `mlx-qwen3-asr-upstream-pr/` 已移出本仓库到 `~/code/mlx-qwen3-asr-upstream-pr`（独立仓库，继续用于上游 PR）。
- 提交前定向验证：调度 10 项、麦克风 9 项、音频流 13 项、编辑框契约全通过。日常服务与客户端仍未重启，下述修复需重启后才加载生效。

## 2026-09-19：重启后高延迟与录音结果乱序（代码修复完成，已提交待加载）

- 用户报告两端近期修改后重启，转录稳定高延迟且连续录音结果乱序；授权直接全链路排查，不要求用户额外复现。
- 已有日志确认：09:42 的前句等待20.254秒，后句先返回；10.78秒录音在09:42:50.258提交final，09:44:29.685才进入最终推理，09:44:30.418完成，等待99.427秒而最终处理约0.733秒。
- 根因：客户端50ms→20ms音频块触发服务端`drain_queue`既有20ms等包逻辑，连续到包可长期阻止消费，排空后每消费一块又额外等20ms；`WorkBuffer.pop`又优先最新task，导致旧句被新句插队、饥饿。现有证据不支持把这些等待归因于语言前缀/尾块精度修复。
- [x] 服务端有积压时非阻塞、每轮最多收64包；同socket FIFO、跨socket轮转；仅实际断连时扫描包缓冲，避免正常消费的二次方清理开销。
- [x] 无模型调度回归10项通过；旧代码最初9项中6项失败，新增单句20ms定时用例也确认旧实现失败。麦克风相关22项、编辑框结果流与UI契约通过；final排队/处理耗时已拆分日志。
- [x] 已同步ASR总文档证据及稳定测试入口。尚未重启或部署本轮修复，不能声称日常延迟已实际恢复。
- 用户补充口径：即使刚重启、无其它后台任务、单独一句一句录音也高延迟。已确认积压的是单句自身的20ms音频包；连续录制仅额外触发新句插队，不是高延迟前提。
- 继续核查Runner历史：用户记忆中的“约30秒录音内提前推理”与当前实现不一致。7月6日主仓库`76250e2`/子仓库`25551b0`已明确P0仅流式缓存、final调用Session，提前处理InferenceChunk尚未实现；当前仍如此。本轮队列修复不会自动实现录音内提前推理。重构前外层按60s+4s重叠缓存（68s触发），短句通常只提交一个工作单元；重构后逐包入队却保留20ms等待，50ms→20ms采集块是本次明显回归的触发条件。
- 运行态只读核查：server/client分别于9月19日09:36:11/09:36:13启动，已加载此前两轮修改；本轮调度修复尚未加载。

## 2026-09-19：服务端 wired 常驻修复（进行中）

- 用户授权核查上一轮结论、实际服务配置并修复；保留其它工作区改动。
- 已核对：`config_server.py::Qwen3ASRMLXArgs` 为 prewarm=True、wired=True、limit=auto，模型为1.7B-8bit；当时日常服务为9月18日15:49启动；9月19日本轮故障排查已核实用户随后重启，server现为09:36:11启动。
- 新发现：Session构造即加载并eval权重，早于 `_startup_initialize_runtime`；仅修改该函数内部顺序不足。MLX 0.31.2源码支持已有buffer补入和降限移出，上一轮“不追溯/不会撤pin”不能作为普遍事实；正在补当前环境稳定测量。
- [ ] 先完成独立进程旧/新顺序对照，记录系统wired、active和cache；不以API成功替代物理驻留证据。
- [ ] `capswriter_runner.py` 在Session创建前设预算、加载/预热后按active收口；保留显式额度与关闭语义、启动失败恢复、cleanup原始预算。
- [ ] 在现有Runner测试中覆盖加载前生效、收口和故障路径，先观察旧实现失败再验证修复。
- [ ] 同步现有架构规格与稳定入口，运行定向测试、真实模型验证及代码复核。日常服务尚未重启，长期空闲效果单独验收。

## 2026-09-18 至 19：客户端麦克风启停优化（已实现，待日常复验）

- 用户确认：保留短按切换大小写、长按才占用麦克风及现有 200ms 阈值；优化额外等待与正常松手后的关闭可靠性。本轮范围为客户端，不调整服务端推理。
- 证据：现有日志最近100次 down→首帧中位383ms、P95 443ms、最大1506ms；长按计时中位270ms，设备打开中位52ms、最大1187ms。15:49:28 正常松手（已录约10秒）后关闭路径超时5秒，停止事件已完整到达，不归因于连续点按。日志尚不能区分 active 查询、abort 或 close 的具体卡点。
- [x] 音频流生命周期：正常建流复用已初始化的设备列表；只在必要恢复时刷新，禁止关闭未完成时重载底层库；关闭状态与成功/失败留痕分离，完成回调不得直接重启原生流。
- [x] 按键与录音任务：保护启动发布和结束过程，正常松手不丢停止请求；保留200ms判定及短按行为，补分段耗时。
- [x] 采集完整性：修复跨缓存阈值时漏掉当前音频块；定向验证每个块仅处理一次。
- [x] 回归与复核：用可控阻塞/异常验证流启停、并发松手和恢复边界，运行客户端相关现有测试；更新稳定文档入口。真机关闭偶发故障需单独复验，不以模拟测试宣称完全解决。
- 工作区已有编辑框和服务端改动已随本轮一并提交；不重启日常客户端，完成代码验证后报告加载方式及残余风险。

- 实现结果：macOS 20ms/low 延迟；内建设备正常路径不重载库，无内建时仍刷新默认输入；回调统计移到loop、回调外恢复、严格检查关闭错误码，关闭未完成不重开。控制器FIFO与退出保护、完整启动发布保护、每录音独立队列、取消语义及缓存漏块修复均已落地。
- 验证：22项客户端定向测试通过；跨阈值漏块、松手后状态复活两个回归用例在备份旧源码上均失败，新代码通过。现有编辑框结果流及UI契约通过，语法与diff检查通过。主会话按更新后的 requesting-code-review 亲自复核，未采信中断的独立审查结果。
- 本机独立进程测量（各8次，非完整键盘/正式客户端路径）：开流请求→首帧中位138.73ms→105.48ms，约减少33ms；关闭中位117.67ms→144.43ms。代价是回调退出再释放多约27ms。原始记录 `diagnogs/mic_lifecycle/20260919-hardware-probe.json`；短时样本不能证明长期偶发卡死已消失。
- 补充本机6次启停（首帧后保持0.05/0.25/1/10/0.3/0.1秒）全部确认原生关闭成功，close耗时134.77–147.30ms，记录 `diagnogs/mic_lifecycle/20260919-native-close-check.json`；此验证不等同于日常偶发故障已根治。故障暂停同时令菜单栏进入error，下一次成功开流恢复麦克风可用标记。
- 残余边界：真正卡死的原生关闭无法靠Python线程强制释放，当前做安全隔离与准确提示，仍可能需要菜单栏重启；日常客户端未重启，本轮没有部署新版本或修改服务端运行态。

## 2026-09-18：吞尾服务端差异继续排查（前轮口径）

- 用户明确：两端使用同一份既有音频、仅经过服务端；客户端采集风险不能解释本次平台输出分歧。本轮只沿服务端解码、声学特征、编码器末端长度/补齐、提示词及EOS决策继续定位。
- 主 Agent 独立完成，不使用子 Agent、不另写 plan；保留上轮语言修复和既有工作区改动。不以Windows作为绝对真值，也不拿真值缺失替代差异归因。
- 第五轮已定位并修正：MLX尾块未经官方pad_sequence补齐直接卷积，与真实非零bias下的Qwen/Windows语义不一致。只改变这一点，旧/新语言前缀下均让20条尾缀候选中的同样4条恢复Windows尾字；12条其它对照无实质变化。修复位于子仓库encoder.py，测试旧实现3项失败、修复后267项通过。
- 第二处已定位差异：MLX按原模型配置8秒窗口做声学注意力，Windows ONNX对有效token全局注意。仅取消窗口隔离让9条尾缀恢复Windows正文（与上组重叠1条），全部>8秒，多条仅8.1–8.8秒。未擅自把全局注意力设为默认，待验证与现有两项修复的交互及较长录音代价。
- 其余隔离：换Windows Mel无正文变化；完整Windows prompt改变3条尾缀但一条多出“吗”，不能等同于全面修复。证据在 `diagnogs/tail_boundary/`，本轮256次诊断生成均EOS；正式适配器33条/66次验收完成，64组短录音raw token/prompt/正文与诊断修正一致，长录音新默认完全不变、旧入口仅标点变化。当前两修复共使6/20尾缀对齐Windows，其余仍未全部解决；267项测试及语法/diff检查通过，日常服务未重启。

## 2026-09-18：Qwen3-ASR MLX 已知问题修复（当前优先任务）

- 最新授权：用户要求至少修复一项已知问题后停下汇报；由主 Agent 直接完成，不使用子 Agent，不另写 plan。
- 本轮选择修复中英混说变全英文：CapsWriter 自动语言模式使用正文前缀；保留显式语言、上游裸 API 默认和可回退开关。吞尾补静音因仍有词义歧义暂不启用。
- 当前状态：正文前缀修复已落地包内4个源码文件；默认仅CapsWriter启用，裸Session保留原默认；显式语言、对齐需求保持旧入口，正文模式language=unknown，支持开关回退。第四轮当时吞尾未修，最新部分修复见上方第五轮。
- 验证：175项定向测试通过；正式适配器分包运行32条录音/64次生成，旧基线32/32复现、新结果32/32与第三轮正文诊断一致，三条全英文异常全部恢复中英混排；9条对照仅1条术语词形改变，未判优劣；6次静音均空。记录见 `diagnogs/symptom_probe/20260918-151509/acceptance.json`。
- 长录音补验：31.2秒/2个内部chunk，修复前后正文完全一致；语法及主/子仓库差异检查通过。
- 部署状态：未重启日常服务；源码待下次重启加载。下方第一至三轮的“不改生产/不做实验”等边界是各轮历史记录，以本条新授权为准。

## 2026-09-17 至 18：前三轮排查记录

- 第三轮（用户在第二轮建议后授权按助手思路继续）：转入独立诊断进程的小样本单变量验证，优先12条语言样本与20条吞尾候选；不改生产实现/默认参数、不重启服务、不覆盖原始转录。与此前暂停扩展实验的第一/二轮边界按时间区分；本轮只执行针对已有具体假设的验证。
- 第三轮实测结果：32条独立录音、140次有限生成完成，64次重复/独立基线全部与已保存Mac输出一致；均EOS。记录位于 `diagnogs/symptom_probe/20260918-101038/`、`20260918-101222/`、`20260918-101358/`；可复现脚本 `tools/asr_symptom_probe.py`。
- 语言前缀：仅指定Chinese或仅预置正文分隔符均使3/3全英文异常恢复中文+英文术语；6条中文/混说正常对照无实质变化，3条英文术语对照中1条词形改变，未判优劣。仅正文模式language=unknown，不能直接称无代价替换默认。补静音对这3条语言异常无效。
- 吞尾：20条原始候选均EOS、8–50token/128–344预算。固定预算仅补150ms静音使11条增加尾缀，其中10条与Windows归一化对齐；剩余1条为「风险」而Windows/仅正文前缀为「风格」，尚待听音判定。仅正文前缀改变2/20尾缀。补静音对12条语言样本全部无正文变化。证据表明尾部边界敏感，未证明所有补全正确或排除实际采集丢尾。
- 第三轮收尾：生产实现/配置未改，诊断脚本与现有文档已更新；语法、记录完整性与单变量约束检查通过。下一步优先核对原音频尾字真值和扩大必要正常对照，暂不全局改语言、预算、temperature或补静音。


- 第二轮范围（用户最新授权）：继续审查重构及参数，与官方 Windows 链路和 fork 所基于的 MLX 上游默认进行比较，给出下一步排查结果及调优建议。本轮先做源码/已有证据对照，不修改生产参数、不新增模型实验；两个独立重点仍为吞尾与混合语言变全英文。
- 第二轮结果：固定官方Windows `84912d5`、MLX上游 `1e28932`；Windows四个GGUF核心文件与本机字节一致，fork六个核心文件与f069a0f一致；当前MLX上游五个关键函数AST一致，15项核验通过。源码/散列位于 `diagnogs/default_comparison/20260918/`。
- 参数建议：优先对照auto、仅指定Chinese、Windows式仅正文前缀三种语言入口；temperature保持0以隔离因果。Windows的0.4/top_k50是其集成默认，Qwen参考vLLM自身默认0，不能把照搬Windows叫作恢复模型官方默认。尚未实施参数试验或生产修改。
- 吞尾细化：分别看采集样本完整性、EOS、length、repetition；truncated仅指length。Windows遇重复有加温重试，MLX直接停止；现有40条均EOS，未覆盖全部吞尾候选。官方Windows也有recording回调门控，不能当Mac独有回归。
- 新确认诊断缺口：Runner的language/chunks/逐chunk token预算未随Result和转录Markdown保存，只有部分日志；回调未保存ADC采集时间。建议先补必要留痕，再做小样本单变量对照，详见ASR总文档第二轮表格。


- 用户最新澄清（2026-09-18）：整体精度是否下降尚未确认，也可能来自使用要求提高；Windows 同样并不完美。当时重点是检查此前 Runner 重构是否犯了代码错误，不能预设 macOS 精度下降，也不能用跨后端差异直接证明重构回归。
- 两条独立问题（用户 2026-09-18 进一步明确）：吞尾部、中英混说变全英文都可能真实存在，分别追踪；不得因整体退步未证实或两端尾缀差异双向存在而排除它们。
- 全英文定位：3 条明确样本在已有记录中的原始生成 token 已为英文，重建旧 Session 与两种 Runner 调用 token 完全一致；均以 EOS 结束、未耗尽预算。问题出现于当前模型生成阶段，不是 Runner 后处理翻译；尚未确定生成英文的更深原因，亦不等价于复原历史依赖。
- 吞尾定位状态：`shortcut/task.py:249` 先关闭 recording，`audio/stream.py:79` 对随后回调直接 return，按需模式关闭活跃流不保证交付 pending 音频。这是采集收尾的具体风险点，尚未证实对应用户病例。8 月 23 日 `d9e79d4` 新增 abort，但旧 close 对活跃流也丢弃 pending 缓冲（本机 sounddevice.py:1160 文档），不能仅凭 abort 提交认定新回归；recording 门控早于 Runner。现有服务端批跑无法验证松键前未写入原始录音的尾音。


- 用户目标：接管云端交接，核查 Runner 重构是否改变旧 Session 推理语义并造成精度回归；当前不进入架构重设计；第二轮扩展为默认值比较与调优建议，尚未实际调参。
- 对照材料：`diagnogs/assets/`、`diagnogs/transcripts/`、`diagnogs/transcripts_windows/` 各 9313 条，均为既有录音的服务端批跑；Windows 为 Qwen3-ASR 1.7B 4bit。Windows 输出仅作对照，不是人工真值。
- 已复核统计：统一 `（空）` 后，5499 条完全一致、1631 条仅格式差异、2183 条实质性差异；Mac 空 233、Windows 空 217、共同空 217、Mac 独有空 16。格式归一化规则是小写化并移除 Unicode 标点、符号、分隔符及空白。
- 仓库归属澄清（用户 2026-09-17 确认）：Custom 分支上传在另一个 fork，并非当前本地关联的云端仓库；不得据当前 remote 缺少分支或 commit 推断历史已丢失。本轮已有完整本机源码，不依赖远端恢复。
- 关键进展：本机子仓库 HEAD 即 `25551b0df31e4bfa0566ca6ae4e70e3c16b9c2c5`，Runner 源码完整可读。相对 `f069a0f` 只新增 Runner、导出、测试和事件记录，Session/模型/解码器实现未变；Runner 默认 `max_new_tokens=None`，final 仍复用 Session。
- 模型身份已复核：8bit 目录内 config/model card 错标 4bit，但权重张量打包维度和已保存的运行态记录一致，实际 198 个量化模块均为 8bit；不是误加载 4bit。不得仅据目录名或 model card 判定位宽。
- 排查顺序（用户 2026-09-17 纠偏）：首要任务是审查代码变更。沿主仓库 `76250e2`、前后续提交及子仓库实际 diff 逐项判断新旧语义；两端已有样本作为比对信息，不再扩展运行实验。已生成的运行记录仅保留为辅助证据，不替代代码审查。
- 当前边界：已有编辑框相关工作区改动保留；不修改生产推理实现、不重启日常服务、不覆盖两端转录原始资料。
- Windows 来源补充（用户 2026-09-18 确认）：使用用户 fork 的 CapsWriter-Offline Custom 分支，该分支未修改底层推理管线，可按官方 Windows 路线理解。精确 commit/运行配置快照尚未取得，不将本机源码逐字等同于当时执行版本。
- 收尾追加审查：预热调用不经任务缓冲，generate 每次创建新 KV cache；Worker 同 task_id 内 FIFO、final 数据先入缓冲再 finalize，清理按 socket 断连，正常连接路径未发现提前清空。旧流水线的 `@@` 删除/空白压缩被绕过属于输出清洗差异，外部 aligner 主要影响 text_accu/时间戳；这些均未形成普通短句识别回归的证据。
- 第一轮收尾（2026-09-18）：已审查主仓库 `76250e2` 与子仓库 `f069a0f → 25551b0` 的参数/PCM/语言透传/分段差异，尚未锁定普通短句普遍退步的 Runner 回归点。`max_new_tokens=None` 保留；16k mono patch 无额外逐包信号处理；final 仍进入同一 Session。
- 两端代码差异已定位：GGUF `_build_prompt_embd` 在 auto 模式预置 `<asr_text>`、空 context 使用通用 system 文本，且聊天边界换行与 MLX 不同；GGUF 默认 temperature=0.4、top_k=50、随机种子及重复熔断重试，MLX 默认 greedy=0。这些是既有后端差异，不能直接断言是 Runner 新引入或已证实的精度根因。
- 文本差异画像：2183 条中 1708 条字符对齐相似度≥90%，1485 条仅一个连续差异区；1577 条差异字符不含拉丁字母/数字，606 条含；明显 Mac 全英文/Windows 含中文候选 3 条。严格前缀关系下，Mac 少尾缀 20 条，反向 27 条。指标有交叉且不是真值错误率。
- 长音频：外层 60s+4s overlap/拼接被 Runner 绕过是真实行为变化，但仅 12 条达到 68s，且这 12 条两端字符相似度均>95%；不能据此认定长音频劣化或解释其余 2171 条。
- 独立旧缺陷：`core/client/audio/recorder.py:142` 在首次越过录音阈值、缓存非空时只发送/保存缓存，漏掉当前 callback 数据；可回溯到 `e80e2181`（2026-01-10），早于 Runner。本轮未修，不归因为同一已存录音的跨后端转录分歧。
- 验证与边界：本轮从 9313 对原始 Markdown 全量复核统计；没有新增模型实验。前轮已保存的 40 条同环境旧调用/Runner 对照均一致、无 length 截断，仅作为辅助证据，不能替代历史依赖环境复原。细节见 `docs/ASR调优总文档.md` 第一轮结论与 `diagnogs/runner_regression/text_difference_profile.json`。本轮生产代码未修改；阶段汇报后停下。



## 2026-08-24 自主执行恢复状态（8 月 25 日续）

- 当前核心目标：完成「编辑框标注行为口径（2026-08-24 用户重新声明）」的实现、逐任务评审与真实用户路径验收。
- 恢复依据：8 月 23 日旧计划仅作为历史账本；8 月 24 日重定义的 Task 1–7 已分别提交并通过当时的定向验证。随后真机验收发现 Esc 错误推进指针、⌃⌥M 未被原生吞键、面板纵向位置不符合预期，现由主会话按最终口径纠偏。
- 执行计划：`docs/superpowers/plans/2026-08-24-editor-annotation-semantics-redefinition.md`。
- **标注数据 v2 口径（2026-08-24 用户确认）**：新系统只写 `evals/manual_cases/v2/cases.jsonl` 与 `v2/audio/`，每条固定 `annotation_version: 2`；旧 `evals/manual_cases/cases.jsonl` 与旧音频不迁移、不改写，缺版本字段一律视作 v1。后续评测默认只选择 v2，避免把旧的低可信数据混入新版高可信数据集。
- 当前状态：🟢 2026-08-25 用户已实测接受核心功能并授权推送。标注状态机、Esc/direct 两类标记、⌃⌥M、菜单图标区分与 Enter 即时上屏均通过真机复验；Enter 期间即使切换焦点，或面板失焦后重新点击确认，仍会回到录音开始时保存的目标应用并立即上屏。
- **Enter 上屏时延口径（2026-08-25 用户最终确认）**：按下 Enter 的瞬间必须直接上屏，不接受约半秒的固定等待。顺序固定为“同步发布 `editor_confirmed` 指针 → 写剪贴板/请求恢复原目标应用 → 进程内 Quartz 立即注入 Cmd+V → 音频归档/日记/`corrected` v2 落盘”。禁止无条件 250ms 焦点 sleep、禁止 pbcopy 后额外 50ms sleep、正常路径禁止启动 `osascript`；AppleScript 只作 Quartz 注入异常时的兜底。
- **编辑框圆角瑕疵修复（2026-08-25）**：截图确认 `NSVisualEffectView` 直接作为 `NSPanel` 根内容视图时，系统毛玻璃背板仍按矩形窗口合成，导致底部两角露出白色矩形。现已在窗口与毛玻璃之间增加独立透明圆角裁剪容器，并在面板自适应高度变化后同步毛玻璃 frame；重启后用户真机确认瑕疵已消失。裁剪容器与随系统外观变化的同一毛玻璃层级共用，因此深色、浅色模式统一生效。

---

## 当前目标

- 分支：`mac-dev`，基线：`master`
- 为 macOS / Apple Silicon 新增 `qwen_asr_mlx` 后端，实现 Caps Lock 长按录音、结果返回、剪贴板写入、自动上屏。
- **当前阶段：launchd 双 agent 架构已落地，下一步转入后端推理优化（2026-06-05）。当前优先排查 `qwen_asr_mlx` 在 macOS 上使用 1.7B-8bit 时的精度表现，重点对比 Windows 侧 4bit 路线。**
- 完整架构决策见 `docs/macos-architecture-decisions.md`

---

## 架构

```text
launchd
  ├─ CapsWriter.app/Contents/MacOS/CapsWriter  （client agent）
  │    ├─ NSApplication 主线程 → ErrorBus → status.json / 通知
  │    └─ asyncio 子线程 → CapsWriterClient
  │              ├─ MacOSCapsRemapSession / MacOSCapsF18Bridge / CGEventTap
  │              ├─ AudioRecorder / WebSocketManager
  │              └─ ResultProcessor → 剪贴板 / 上屏
  └─ start_server.py  （server agent）
       └─ qwen_asr_mlx（端口 6016，client 断连 60s 后自行退出）
```

**Ownership：** launchd 管两个 agent 生命周期；server 通过 WebSocket 连接状态自管生命周期；client 独占 Caps remap；CLI 封装 launchctl 统一控制两者。

**明确不采用：** capswriterd（已废弃）；.app 子进程管理 server；单独日志命令；remap repair 命令。

---

## 关键决策

| 决策 | 内容 |
|------|------|
| 发布形态 | 近期 clone + install.sh；远期 .dmg |
| server 生命周期 | client 断连等待 60s；`capswriter stop` 发 shutdown 信号立即退出 |
| 错误提示架构 | ErrorBus 统一内部出口；近期 status.json + CLI 先行；Unix socket 实时推送待 GUI 阶段 |
| CLI start 行为 | 阻塞等待，实时输出，成功或明确失败前不退出 |
| 权限引导（两权限） | **2026-06-22 重订，推翻旧"仅辅助功能"**：辅助功能 + 输入监控**都引导**。丝滑首装：AX 弹框→拨开关→（AX 就绪后程序**补一次 tap 尝试**注册出 IM 条目）→拨 IM 开关→重启一次即用，**用户不必点「+」、不必去 Finder 找 app**。**2026-06-24 二次收敛（已实测生效）**：**砍掉程序内「统一手动指导面板」**——引导只说「打开开关 / 请重启」，**永不弹面板、永不说删/剪条目**；stale 与一切疑难统一交给 `capswriter reset-permissions` 命令兜底。详见架构决策第六节『2026-06-24 二次收敛』。 |
| 连接状态通知 | 每次 WebSocket 状态变化发系统通知，冷启动第一次也通知 |
| 用户心智 | 运维层透明（只操作 CapsWriter 整体）；故障层用「识别引擎」指代 server |
| 菜单栏 GUI | 采用**自定义矢量 mark**（"会说话的⇪"：气泡 + 波形 + Caps Lock）作菜单栏 template，**放弃** SF Symbols `waveform`；NSImage 原生读 SVG（`_NSSVGImageRep` 矢量，任意倍率清晰）+ `isTemplate` 深 / 浅色自适应 + `autosaveName` 固定位置；旧系统（<13）@2x PNG 兜底。**下拉菜单已落地**（见任务 M8）：纯原生 `NSMenu`+`NSMenuItem`（无自定义视图，自动继承系统 Liquid Glass 材质 + 深浅色自适应），SF Symbol 模板图标。五项：状态表头(禁用，按 ErrorBus 快照刷新) / 复制最近结果(无结果置灰) / 编辑热词(open -t hot.txt) / 重启 CapsWriter(=`capswriter restart`) / 退出 CapsWriter(=`capswriter stop`) |
| 显示名称 | `CapsWriter for macOS` |
| **编辑框标注行为口径（2026-08-24 用户重新声明；真机验收纠偏后现行唯一口径）** | **先分清两个概念**：数据集落盘与内存中的“可标记上一条”指针不是一回事。指针只有 `editor_confirmed`、`direct` 两种；落盘 status 只有 `corrected`、`final_unreliable`、`raw_unreliable` 三种。**编辑框 Enter**：确认关闭后立即把指针推进为 `editor_confirmed`；自动写一条 raw+用户确认 final 的 `corrected`；恢复录音开始时的目标应用并上屏。之后菜单与 ⌃⌥M 表示“标记上一条真值不可靠”，显式标记时追加 `final_unreliable`。**编辑框 Esc**：该条彻底不进入标注域——不落任何数据、不创建任何指针类型、不移动既有指针；Esc 后 ⌃⌥M 仍命中 Esc 之前最近的合法 Enter/direct 条。面板文本非空时只写剪贴板、不自动上屏；清空后不覆盖剪贴板；两者都恢复录音开始时的目标应用，音频/日记/归档既有链路继续执行。**非编辑框 direct**：默认绝不写入数据集；只有成功写入剪贴板后才把指针推进为 `direct`。之后菜单与 ⌃⌥M 表示“标记上一条转录有误”，显式标记时才写 raw-only `raw_unreliable`。**无效条与待编辑条**：都不落数据、不推进指针；无效条只由时长规则判定并保留既有输出/音频/日记链路，框内等待编辑的内容也不叫上一条。**热键**：固定 ⌃⌥M，由 macOS active event tap 吞掉 keyDown/keyUp 后异步执行，禁止透传导致系统错误音或特殊字符。**菜单**：完全由当前指针类型决定标题与动作；无指针时禁用；Esc、无效条、待编辑均不得改变菜单语义。**编辑框 UI**：面板只有编辑区域，按宽度自动换行；Enter 确认，Shift+Enter 插入换行；高度自适应并设上限，超限后可滚动；窗口水平居中、纵向靠上，顶部边界固定，高度只向下增长或从底部缩回；面板打开期间长按 Caps 不启动新录音。**通知**：标记成功通知显示被标记条的文本摘录，有 final 优先 final，否则 raw。新版记录只写 v2。 |
| 信号处理 | SIGTERM：set_wakeup_fd + SigtermWatcher 守护线程（NSApp.run() C RunLoop 期间 Python signal handler 无法执行）→ _critical_cleanup() → os._exit(0)；SIGINT 双击确认 |
| 流式识别策略 | 当前阶段**不**把“产品级流式识别 / 流式显示”作为优先目标。Qwen3-ASR 的 decoder 虽具备自回归逐 token 输出能力，但要做成稳定的端到端流式体验仍需额外的 chunking、稳定前缀/不稳定尾巴管理与中间结果提交策略；现阶段先聚焦最终结果精度 |
| MLX 后端演进路线 | 当前 `qwen_asr_mlx` 只是一层最小适配，后续精度优化主路线改为：**fork `mlx-qwen3-asr`，接管中层推理编排**（prompt 组装、language/context 策略、generation config、chunking、aligner 接法），而非继续把 `Session.transcribe()` 作为黑盒 |
| 模型常驻内存（权重 wiring）+ 启动预热 | **已敲定，待 editable package runner / 中层编排落地后实施**（2026-06-22）。解决 `qwen_asr_mlx` 偶发首次识别延迟极高（基本可确定是权重被 macOS 压缩/换出，再次推理需搬回物理内存）；**不**承诺解决"内存被别的程序激烈争用时推理变慢"：**不检测"模型是否还热"（RSS/瞬时压力/粘性压力全否决，系统内存管理是黑盒不可靠观测），改用 `mx.set_wired_limit` 把权重钉成 wired 常驻内存、不可换出**。配套**一次性启动预热**（付清 MLX kernel 编译，与开关无关，始终做）。wiring 做成 **server 可选项**（`Qwen3ASRMLXArgs`，默认开，文档写明可关，用户跑高占用软件时不该死赖内存），由 server 透传到 editable package；自适应按 `get_active_memory()` 定大小、卡 `cap*0.6` 双保险、不需 sudo、仅 macOS 15+。**具体 wired limit 计算、`mx.set_wired_limit` 调用和预热时机归 fork 的中层编排层，不在 CapsWriter 适配层**，避免重复搬迁。完整推理留痕见 `docs/macos-architecture-decisions.md` 第九节 |
| App 图标 | `.icns` 放 `assets/icon/app-icon.icns`（源）→ 拷入 bundle `Resources/` + `Info.plist` `CFBundleIconFile=app-icon` + 重签名；`build_launcher.sh` 每次构建自动同步。LSUIElement 不进 Dock，图标体现在 Finder / 简介 / 权限列表 |
| 通知后端 | `osascript`（归属脚本编辑器=卷轴）→ 改 **`UNUserNotificationCenter`**（CapsWriter 身份）；裸跑无 bundle 时回退 osascript；调用前用 `bundleIdentifier()` 防 abort。**横幅图标破图问题已 park**（见 `docs/bug-report-notification-icon.md`） |
| 键盘失败处理 | 见 `docs/macos-architecture-decisions.md` 第六节。**回调非阻塞铁律**（业务甩工作线程队列）；失败分类（timeout/丢keyUp=自救带预算，撤权/创建失败/RunLoop退出=fatal）；fatal 单路径=恢复 remap+通知+引导重授权后重启，**删 15s 静默循环**；撤权时主动 `CFRunLoopStop` |
| 权限恢复 UX | **2026-06-22 重订（取代"渐进探测式/仅辅助功能"）**：**两件事分离**——(a) 让条目出现 由程序自动（AX 弹框 / IM 靠 AX 就绪后补 tap 尝试注册，request API 实测无效已弃）；(b) 拨开关 永远是用户的活。判下一步只看 **探测 + tap 心跳** 两个信号；**绝不因权限退出进程**（杜绝 fatal→退出→KeepAlive 死循环）。**防死循环铁律**：只有「探测全 granted 且 tap 心跳死」才提示删条目，其余一律「打开开关」。stale 真假靠**单次启动校验 ping（option C）**确诊；运行期体检仍用 `CGEventTapIsEnabled`。**2026-06-24 收敛**：判 stale 后**不再进程内弹手动面板**，改为通知用户运行 `capswriter reset-permissions`（先停 client → tccutil reset 两权限 → 重启从零重走）。详见架构决策第六节『2026-06-24 二次收敛』。<br>**2026-06-25 实测收敛（放弃精确编排 TCC）**：多轮实测确认 macOS 两权限弹窗顺序（AX vs IM 先弹）与 **IM 条目何时出现在列表里**都**不可由程序可靠控制**（同一从零路径出现多种表现）——这是 TCC 的固有不确定性，非本项目 bug，**决定不再投入精确编排**。对策只一条：让引导**对任意顺序都健壮**——IM 通知文案改为不再断言「条目已就位」，而是显式兜底「若列表里没有 CapsWriter，请点「+」搜索并添加」（`macos_permission_guide.py` 阶段 3）。配合 `reset-permissions` 兜底，口径足够。 |

---

## 任务看板

| 任务 | 状态 | 说明 |
|------|------|------|
| qwen_asr_mlx 接入 | ✅ | 真实音频闭环验证通过 |
| macOS 输入链路 | ✅ | 长按录音 → 结果 → 剪贴板 → 自动粘贴全链路验证 |
| .app bundle + launcher_embed | ✅ | Mach-O C 启动器，hardened runtime，麦克风胶囊显示 CapsWriter |
| AVFoundation 权限弹窗 | ✅ | 首次启动弹出麦克风授权对话框 |
| CGEventTap 失效恢复框架 | ✅ | 通知用户 + 打开设置 + 每 10s 重试；已消除 pynput 降级 |
| **M1：launchd 双 plist 架构** | ✅ | capswriterd 归档；两个独立 plist；capswriter CLI 改走 launchctl；start_server.py 加 SIGTERM→exit 0 |
| **M2：server 60s 自退出** | ✅ | `_watch_connections()` 后台协程；首次有连接后开始监控；断连 60s 则 app.stop()+os._exit(0) |
| **M3：ErrorBus + status.json** | ✅ | `ErrorBus` 类；状态变化时写入；5s 心跳；退出删除；连接/录音状态已接入；traceback 补全 |
| **M4：CLI 改进** | ✅ | `start` 阻塞轮询 status.json 等 ready；`status` 读 status.json 展示完整快照 |
| **M5：Accessibility 引导优化** | ✅ | osascript 分支弹窗（只弹一次）；15s 重试；ErrorBus wire accessibility_ok；CLI start 超时有具体提示 |
| **SIGTERM 修复** | ✅ | set_wakeup_fd + SigtermWatcher 守护线程；_critical_cleanup() + os._exit(0)；capswriter stop 现可在几秒内干净退出 |
| **M6：菜单栏图标** | ✅ | 自定义矢量 mark 作 template（`start_client_macos.py:_install_status_item`）：NSImage 原生读 SVG（`_NSSVGImageRep` 矢量）+ `isTemplate` 深浅自适应 + `autosaveName` 固定位置；旧系统 @2x PNG 兜底 + 文字兜底；代码正式素材在 `assets/icon/capswriter-menubar-template.{svg,png}`（v2），设计/调试件留在 `assets/branding/` |
| **M8：菜单栏下拉菜单** | ✅ 代码+冒烟测试，待运行时复验 | 2026-06-22。纯原生 `NSMenu`+`NSMenuItem`（**铁律：不塞自定义视图**，否则破坏系统材质）→ 自动继承 Liquid Glass + 深浅色自适应；SF Symbol 模板图标同样随外观反色。`_StatusMenuController(NSObject)` 兼 target+`NSMenuDelegate`，`menuNeedsUpdate:` 刷新表头文案 + 复制项置灰。**状态表头用彩色 emoji 圆点（color glyph，禁用项也显色）按 `ErrorBus.state` 着色：🟢运行正常(ready) / 🔵录音中(recording) / 🟡识别引擎未连接(connecting) / 🔴客户端故障(error，或 ready 但 microphone_ok=False) / ⚪️启动中(starting)**——直接用 state 字段避开启动期误报红灯。五项：状态表头(禁用) / 复制最近结果(`NSPasteboard`，回退 `last_recognition_text`) / 编辑热词(`open -t hot.txt`) / 重启(`capswriter restart`) / 退出(`capswriter stop`)。**退出/重启都会 SIGTERM 杀掉 client 自身，故 detached 派生（`start_new_session=True`），解释器用 `.venv/bin/python` 与 install.sh 一致**。ErrorBus 新增 `snapshot()`。冒烟测试：菜单结构/selector 解析/复制置灰+写剪贴板/状态文案映射全过 |
| **App 图标绑定** | ✅ | icns 绑入 bundle + `Info.plist` + 重签名 + `build_launcher.sh` 自动同步；Finder/简介/权限列表显示正确 |
| **通知原生化（UN）** | ✅ | `UNUserNotificationCenter` + osascript 回退 + bundleIdentifier 防 abort；**横幅图标破图已 park** |
| **M7：键盘捕获重构** | ✅ 代码+单测 | 回调非阻塞队列；丢 keyUp 用 `CGEventSourceKeyState` 对账自愈；fatal 单路径（删 15s 静默循环）；删 pynput/B 残骸 |
| **M7.1：永不冻结（实测纠正）** | ✅ 撤辅助功能已实测通过 | 根因 = macOS 撤辅助功能发的是 `DisabledByTimeout`（非 `DisabledByUserInput`），旧码盲目 re-enable 死 tap → 冻结且不弹提醒。修：①不变量「默认安全态=tap 禁用=键盘正常，绝不盲目 re-enable」②`_go_fatal` 先 `CGEventTapEnable(False)` 放行再善后 ③`_on_timeout` 查 `AXIsProcessTrusted` **回调自检**打断 re-enable 死循环（无需线程）。「永不冻结」由系统超时窗+不 re-enable 保证，**不依赖外部检查** |
| **M7.2：外部体检（自检盲区）** | ✅ 代码，待复验 | `listener.check_health()` 运行期只保留 `CGEventTapIsEnabled` 这一条黑盒判据，**复用 5s 心跳**（`mic_runner._heartbeat_task`→`bridge.check_health`），**不**把 `IOHIDCheckAccess` 当 fatal 条件。**补充（2026-06-22）**：「enabled 却静默收不到事件」的 stale 死 tap 是 `CGEventTapIsEnabled` 的盲区，现由**单次启动校验 ping（option C）**覆盖——只在 `start()` 后打一发，运行期不发 ping。 |
| **M7.3：就绪通知门控** | ✅ 代码，待复验 | `result_processor` 不再用权限位去“猜”键盘是否已就绪，而是直接读取 bridge 的真实运行态：只有 active `CGEventTap` 已成功建立才会写 `state=ready` 并发「CapsWriter 就绪」；否则落到 `state=error` + 「键盘接管未就绪」。输入监控不再参与 ready 门控。 |
| **A：server 单例守卫** | ✅ 代码+单测 | 端口自检前置到模型加载之前（`app.start()` 开头）；被占则 `os._exit(0)`（KeepAlive 不重启），避免重复实例先加载模型；删掉 launchd 下会崩的 `input("按回车")`，改兜底 exit 0。**待运行时验收** |
| **B+C：权限引导重写（两权限 + 状态机）** | ✅ **2026-06-24 实测通过**（干净首装 / 撤辅助功能引导 / 通知文案 / option C ping 均正常） | 2026-06-22 整体重写（推翻"仅辅助功能/渐进探测"）。`macos_permission_guide.py`：四象限工具箱（辅助功能 `AXIsProcessTrusted`/`AXIsProcessTrustedWithOptions(prompt)`/面板；输入监控 `IOHIDCheckAccess` 仅作提示/面板`Privacy_ListenEvent`新增）+ `PermPhase` 全局时效状态 + `run_guide` 状态机（辅助功能先行→AX 就绪后注入 `try_register_im` 补一次 tap 尝试注册 IM→引导 IM；**防死循环铁律**只在「探测全 granted+心跳死」弹统一手动指导）+ 统一手动指导文案。`macos_f18_listener.py`：被动心跳 `_last_event_ts` + 合成 ping `_probe_alive`/`tap_healthy`（option C 单次启动校验）+ `attempt_im_registration`（IM 注册手段；request API 实测无效已弃）。`macos_caps_f18.py`：`start()` 建 tap 后 ping 确诊真活/stale，`_handle_tap_failed` 注入回调跑状态机、上报 `perm_phase`、**绝不退出进程**。`error_bus.py`：加 `perm_phase` 字段。四文件 py_compile 通过。**合成 ping 真能流过 HID tap、从零首装、撤辅助功能引导均已 2026-06-24 实测通过。** <br>**2026-06-24 二次收敛（已实测）**：砍掉 `STALE_MANUAL` / `dialog` / `run_guide` 的 `tap_healthy` 参数 / 手动指导面板文案；`run_guide` 签名收为 `run_guide(notify, *, try_register_im, on_phase)`；阶段 4 改为通知「权限已就绪请重启」；stale 交给 `reset-permissions`。`macos_f18_listener.check_health()` 撤回 AX 检查（tccutil 非真实场景）。 |
| **G：菜单栏绿点不灭（撤权后状态不更新）** | ✅ 代码，待复验 | 2026-06-24。根因：菜单栏圆点读 `ErrorBus.state`，而 `result_processor` 只在**连接状态变化**时才用 `bridge.is_tap_available()` 重算 state；运行中撤辅助功能走 `_handle_tap_failed`，只改了 `accessibility_ok=False`、**没动 `state`** → 圆点停在 `ready`（绿）误导用户「以为还在工作」。修法：`macos_caps_f18.py` 的 `_handle_tap_failed` 与 `start()` stale 分支都改为 `eb.update(state='error', accessibility_ok=False)`，圆点立刻转红「键盘接管/权限未就绪」；撤权后无人会刷回 ready（除非重连），稳定显示红直到重启。 |
| **H：双实例看门狗 + remap PID 守卫** | ✅ 代码+实测 | 2026-06-24。(1) `_critical_cleanup()` 在坏态 tap 上调 `CGEventTapEnable(False)` 可能死锁 → SIGTERM 后进程变僵尸 → 菜单栏重启时与新进程并存双实例。修：`start_client_macos.py` 在 `_critical_cleanup()` 开头起 2s 看门狗线程，挂住则 `os._exit(0)` 强退（**2026-06-25 纠正**：原写 `os._exit(1)` 是回归——非零退出会被 client plist 的 `KeepAlive(SuccessfulExit=false)` 当崩溃复活，自己造双实例；看门狗职责只是「保证退出」而非「报告失败」，故用 0）。(2) 杀旧僵尸进程时其 cleanup 会 `restore()` 清掉系统全局 hidutil remap，连带把**新进程**的 Caps 接管也清了（用户现象「caps 接管突然失效」）。修：`macos_caps_remap.py` 的 `restore()` 加 PID 归属校验——state 文件 `client_pid != 自己` 则跳过 restore。 |
| **I：弹窗顺序（输入监控抢先于辅助功能）** | ✅ 代码，待复验 | 2026-06-24。根因：`bridge.start()` 无条件 `_listener.start()`→`_create_tap()`，而 `CGEventTapCreate` 本身会触发「输入监控」TCC 弹窗+注册 IM 条目 → 从零启动时 IM 窗抢在 AX 窗之前弹、且首弹时 IM 条目已在列表（违反辅助功能先行，用户顺手开 IM 再重启则 AX 仍缺、不可预测）。修法：`start()` 用只读 `check_accessibility()` 前置判断，AX 未就绪时**不预创建 tap**，直接起线程跑 `_handle_tap_failed`→`run_guide`（AX 先弹）；AX 就绪后才由 `try_register_im` 补 tap 尝试注册/弹 IM。弹窗顺序恒为「辅助功能 → 输入监控」。 |
| **reset-permissions 命令** | ✅ 代码，待复验 | 2026-06-24。`capswriter.py:cmd_reset_permissions`：先 `_stop_client()` 停 client（避 remap 残留 + 避开 tccutil 撤运行中进程致冻结）→ `tccutil reset Accessibility/ListenEvent com.capswriter.client` → 提示 `capswriter start` 从零重走。stale/疑难统一兜底入口，替代被砍掉的进程内手动面板。README「权限疑难排查」已同步。 |
| **J：去 client KeepAlive（根治双实例）** | ✅ 代码，待复验（需 uninstall→install 重写 plist） | 2026-06-25。根因（与任务 H 同一病灶的架构层）：client plist 设了 `KeepAlive(SuccessfulExit=false)`，而 client 是 GUI app、生命周期本应由用户/CLI/菜单栏自管。macOS 授予输入监控/辅助功能时会**强杀 client**（权限在进程启动时读取），这一强杀被 launchd 当崩溃复活 → 复活的孤儿（常被 LaunchServices 领养到动态标签）与显式 start/手动重启相撞 = 双实例；同一 KeepAlive 也是历史「13s fatal 死循环」的根。修：`capswriter.py:_build_client_plist` **删除 client 的 KeepAlive、保留 `RunAtLoad`**（登录自启）；授权后重启由用户/CLI 显式 `start`。**server plist 仍保留 KeepAlive**（server 非 GUI、不被权限强杀，正常停止 exit 0 不与 KeepAlive 相争，需要崩溃恢复）。⚠️ `cmd_install` 在 plist 已存在时跳过写入，故**复验前必须 `capswriter uninstall` → `capswriter install`** 才能让新 plist 生效。 |
| **K：rpath 检测 4级→3级（误判重签致 TCC 失效）** | ✅ 代码，待复验 | 2026-06-25。`capswriter.py:_launcher_uses_relative_python_rpath()` 检测串写的是 `@executable_path/../../../../.venv/lib`（4 级），而 `build_launcher.sh` 实际产出 3 级 `@executable_path/../../../.venv/lib` → 永远判「未用相对 rpath」→ 每次 `install`/`doctor` 都误判需重建 launcher + ad-hoc 重签 → cdhash 变 → 辅助功能/输入监控 TCC 授权失效（每次 install 后权限都要重授）。修：检测串改 3 级，与 build_launcher.sh 严格一致。 |
| **L：双实例（系统面板拉起后 GUI 重启）** | 📋 **已知问题，文档化不修（2026-06-25 用户拍板）** | **取证已闭环**（日志/launchctl list 实锤），决定**不修**。现象：仅当「本轮 client 是被系统设置面板的『退出并重新打开』按钮拉起」时，再从菜单栏重启会出现两个 client 进程。**良性**：生效的是最新实例，旧的已失效，功能全正常，只是多个空转残留。正常菜单栏重启 / CLI 重启都不触发。**根因（实锤）**：LaunchServices 重新拉起 GUI app → 新进程被领养到动态标签 `application.com.capswriter.client.<ASN>`（脱离静态标签），此刻 `_client_pids()` 的 `pgrep` 短暂查不到该实例 → `_stop_client` 打印「客户端未在运行」一个没杀 → `cmd_start` 又拉一个 = 双实例（日志 00:37:01 实录）。这是 GUI app 同时被 launchd 与 LaunchServices 管理的固有所有权冲突。**用户否决「最小止血（单实例守卫）」**（理由：止不住且现象良性）。**对策**：①README 列已知问题 + 规避建议（授权后用 `capswriter restart`，勿点系统弹窗「退出并重新打开」；IM 条目缺失则点「+」手动添加）；②IM 通知文案已去掉「条目已就位」断言改为「+」手动添加兜底。**清理**：`capswriter restart` 可靠清成单实例。**架构层正解（未排期）**：路线 A——client 脱离 launchd 做成正常 GUI app + 启动自我单实例守卫（`NSRunningApplication`/`LSMultipleInstancesProhibited`）+ CLI/GUI 改为「对唯一实例下指令」，launchd 仅留给 server。 |
| **通知横幅图标** | 🔲 park | 破图，下个会话受控实验（`docs/bug-report-notification-icon.md`） |
| **D：孤儿进程（client 脱离 launchd）** | ✅ 代码+实测 | 根因：client 作为 NSApplication GUI app 被 LaunchServices 从 `com.capswriter.client` 标签**领养**到 `application.com.capswriter.client.<ASN>` 动态标签，`_launchctl_pid(原标签)`/`launchctl stop 原标签` 够不到 → stop 误判"未在运行" → 孤儿存活、start 再起一个 → 双实例。修法：`capswriter.py` 新增 `_client_pids()`（`pgrep -f` 按 .app 可执行文件路径查，**label-independent**）+ `_stop_client()`（launchctl stop 协调 KeepAlive + 按身份 SIGTERM 兜底 + 10s 后 SIGKILL）；stop/start/uninstall/status 全改走它。`restart` 实测：停旧 client→起单实例，无双图标 |
| **E：Caps 长按松手后麦克风卡住** | ✅ 代码+单测，待运行时复验 | 2026-06-22 修复。根因=`task.launch()` 开流（`start_recording_session()` 数百毫秒）期间 `is_recording` 仍为 False，松手 stop 被 `stop_press_to_talk` 丢弃→麦克风永不关闭。修法：`ShortcutTask` 引入 `_lifecycle_lock`+`_launching`+`_stop_pending`，补齐“录音启动中/待停止”语义：launch **锁外**开流（让 stop 能无阻塞登记 pending）、开流后进锁置 `is_recording=True` 并取出 pending，若启动期间已松手则末尾立即 `finish()`；新增线程安全入口 `request_finish()`（启动中登记 pending，否则按 is_recording 决定）；`finish()/cancel()` 改为锁内 check-and-set **幂等**；`stop_press_to_talk` 委托 `request_finish`。开流/关流被串行到同一线程，且“只要开流已开始，松手后必有可达关闭路径”。3 场景隔离单测通过（启动中松手/正常长按/重复 finish 幂等）。<br>—— 原记录：2026-06-19 13:09 复现。最终状态已收敛：**系统级麦克风指示持续亮起，说明麦克风被打开但没有被正确关闭；同时 `Caps` 接管、client 心跳、短按切换均正常。** 关键日志链路：`13:09:55.872` 长按成立并开始 `start_recording_session()`/`stream open requested`/`找到音频设备`；`13:09:56.301` 松手后进入 `stop_press_to_talk()`，但因 `task.is_recording` 尚未置真，被判定为“当前未在录音，忽略 stop_press_to_talk”。由此可知：① 音频流打开动作已经启动，所以系统看到麦克风在录；② 关闭流路径未执行，所以麦克风不会自动收掉；③ 本次并未进入稳定的 `recording=True -> begin/data/finish -> server 识别` 正常链路，server 侧也没有对应新任务，因此这次更接近“识别未真正触发”，而不是“识别后收尾失败”。根因归类：**start/stop 状态机竞态**，不是权限、event tap 或 server 断连问题。 |
| **F：launcher 硬编码 Python 路径** | ✅ 代码+构建验证 | 2026-06-22 修复用户反馈：旧 `CapsWriter.app/Contents/MacOS/CapsWriter` 由开发机编译，Mach-O 含 `LC_RPATH=/Users/edgar/.local/share/mise/.../lib` 且 C 字符串含 `PY_BASE_PREFIX=/Users/edgar/...`，换用户名后 dyld 在 main() 前找不到 `libpython3.13.dylib` 直接闪退。修法：`launcher_embed.c` 运行时读取 `.venv/capswriter-python-prefix`（缺失时退回 `pyvenv.cfg`）定位目标机器 Python base prefix；`build_launcher.sh` 在 `.venv/lib` 创建 `libpython3.13.dylib` 符号链接，并用 `@executable_path/../../../../.venv/lib` 相对 rpath 链接，移除编译期 `PY_BASE_PREFIX`；`install.sh` 负责自动创建/更新 `.venv`、安装依赖、重建 launcher、安装命令；`capswriter install/doctor` 增加旧 launcher 检测与自动重建提示。验证：`bash -n install.sh build_launcher.sh`、`python -m py_compile capswriter.py`、`bash build_launcher.sh`、`otool -L/-l` 显示 `@rpath/libpython3.13.dylib` + 相对 rpath，`strings CapsWriter... | rg '/Users/'` 无命中，`_launcher_rebuild_reason()` 返回 None。 |
| **P3：后端推理精度调优** | 🟡 进行中 | 聚焦 `qwen_asr_mlx`：核对 8bit/4bit 模型选择、上下文/热词能力缺口、音频前处理与解码参数差异，评估是否需要补齐能力或回退默认规格。2026-07-05 已将 fork `git@github.com:EdgarZhong/mlx-qwen3-asr.git` 作为根目录子仓库 `mlx-qwen3-asr` 接入，分支固定为 `capswriter-macos`，`requirements-server.txt` 改为安装本地子仓库包。2026-07-06 P0 已落地：`mlx_qwen3_asr.capswriter_runner.QwenASRRunner` 成为 CapsWriter 和后续评测 driver 共用入口；`qwen_asr_mlx` server worker 按后端分叉，不再走旧 `WorkPipeline` 60s 分片拼接，而是把同一 `task_id` 的音频增量作为 `AudioFeedPatch` 喂给 package Runner；server 外层只保留模型目录解析、队列/传输、`language/context` 请求元信息。验证：本地导入路径已指向 `/Users/edgar/programs/CapsWriter-Offline/mlx-qwen3-asr/mlx_qwen3_asr/__init__.py`；真实 1.7B-8bit 模型最小链路输出 `The quick brown fox jumps over the lazy dog.`。 |
| **ASR 评测脚手架** | 🟢 v1 源数据已备齐，待构建 manifest/driver | 2026-07-05 创建 `evals/` 根目录，约定 `datasets/`、`drivers/`、`results/`、`manual_cases/` 四类材料边界。2026-07-06 新增 `evals/datasets/capswriter_tech_asr_v1/download_sources.py` 和数据集 README，口径改为只下载 v1 所需最小源文件/单 shard，不拉公开数据集全量；`sources/raw/tmp` 已加入 Git 忽略，避免大文件误提交。已下载：`Tech-Sentences-For-ASR-Training` 205 条音频/文本、`Chinese-LiPS processed_val.zip` 约 521MiB、`TED-LIUM3 test` 单 parquet 约 287MiB、`Earnings-22 chunked` 单 parquet 约 368MiB。2026-07-08 用户 HF 访问申请已通过，并在 token 设置中开启 fine-grained token 的 public gated repo read 权限；已补齐 `AISHELL6-Whisper` 的 `AISHELL6-Whisper_info.csv`、`text_sentence`、`w2n.txt`、`metadata.tar.gz`、`test.tar.gz`（约 1.7GiB），下载状态 `complete` 且失败项为空。下载中 Hugging Face Xet 曾在大文件阶段长时间停滞，最终使用 `HF_HUB_DISABLE_XET=1` 完成核心音频包下载。2026-07-06 用户确认：中文真实低语对 v1 很关键；不使用来源不可审计、绕过审批或疑似泄露的数据包，避免污染评测基准授权与复现口径。已收敛语义：`AudioMessage` 是客户端到服务端的协议消息；`task_id` 是一次完整录音/文件转写的完整识别任务标识，等价于 record session / recognition session；服务端 worker 执行单元已统一改名为 `Work`；`AudioFeedPatch` 是 Qwen3-ASR 新路径中 server/worker 按时序喂给 package runner 的内部音频增量；`InferenceChunk` 是 runner 内部真正送入模型的约 30 秒级推理单位。评测主驱动必须接 CapsWriter 服务端后端路径；runner 落地后必须以同一个 `task_id` 调用 package runner，禁止用 `mlx_qwen3_asr.transcribe()` 裸 API 作为主评测结果来源。 |
| **CapsWriter 技术口述 Golden Set** | 📝 待建立 | 2026-07-05 口径收敛并已写入 `docs/ASR调优总文档.md`：评测集只优先覆盖中英文技术口述场景，低语/小声说话必须作为高占比核心条件，而不是少量鲁棒性附加项。原因是便携电脑端用户经常在办公室、会议室、公共空间中压低音量使用，收音条件天然不理想。用户明确不希望自录数据集，后续优先采用公开数据集、固定抽样、可复现增强和必要的公开 TTS/合成样本。评测矩阵按两个正交维度组织：内容场景（中文技术讲解、英文技术词汇、商业/技术会议、命令式口述、中英混合术语）× 声学条件（正常音量、小声说话、真实低语、低增益增强、噪声/混响/远场）。真实低语与简单降音量增强必须分开标注。当前阶段不改产品预处理链路，不新增响度归一化、AGC 或降噪；评测目标是在现有录音、重采样、log-mel 管线下观察模型与接入策略的真实表现。 |
| **权重 wiring + 启动预热** | ✅ 代码+真实初始化验证 | 2026-07-06 落地到 `mlx-qwen3-asr` package 内 `QwenASRRunner`：server 只在 `Qwen3ASRMLXArgs` 传 `enable_startup_prewarm=True`、`enable_wired_memory=True`、`wired_memory_limit='auto'`；Runner 初始化时先用 1s 静音走真实 `Session.transcribe()` 预热，再按预热后的 `mx.get_active_memory()` 自动计算 wired limit 并调用 `mx.set_wired_limit()`。验证：真实 `EngineFactory.create_asr_engine('qwen_asr_mlx')` 初始化通过，package path 指向本地子仓库；prewarm `cost_sec=0.567s`；wired active `2469377260 bytes`，limit `2963252712 bytes`（约 2.76GiB），previous `0`。失败只记录 warning，不阻断 server 启动；cleanup 尝试恢复旧 wired limit。 |
| **P2：Unix socket 实时推送** | 🔲 待实施（GUI 阶段） | CLI 实时订阅 .app 事件流 |
| launchd 端到端测试 | 🔲 待测试 | 重启验证开机自启 |
| FFmpeg 路径确认 | ✅ 2026-08-14 已修复+实测 | 根因：launchd 默认 PATH 是最小集（`/usr/bin:/bin:/usr/sbin:/sbin`），不含 Homebrew 的 ffmpeg → `AudioFileManager` 靠 `shutil.which('ffmpeg')` 判定，PATH 无 ffmpeg 时静默降级存 WAV（体积约为 192k MP3 的 5~6 倍）。修法：`capswriter.py:_build_client_plist()` 给 client plist 加 `EnvironmentVariables PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`；`uninstall→install` 重写 plist 生效。实测：新 client 进程 PATH 含 ffmpeg，`shutil.which` 命中 `/opt/homebrew/bin/ffmpeg`，新录音 ffprobe 验证为真 MP3（codec=mp3，~195kbps，48k/单声道）。TCC 权限不受影响（未重签 launcher）。 |
| **T4：编辑框标注系统** | 🟡 8 月 24 日重定义主体已实现；真机纠偏待复验 | **现行实现边界**：新标注只写 `evals/manual_cases/v2/` 并固定 `annotation_version=2`，旧 v1 不迁移、不混用。Enter 自动写 `corrected` 并发布 `editor_confirmed` 指针；direct 默认不入库，只在剪贴板输出成功后发布 `direct` 指针；显式标记分别写 `final_unreliable` / `raw_unreliable`。Esc、无效条和框内待编辑状态均不落数据、不推进指针。菜单标题与动作只由这两个指针类型决定，无指针时禁用。真机已确认通知横幅恢复正常；本轮待复验项为：Esc 后仍标记前一合法条、⌃⌥M 被 active event tap 完整吞键且无系统错误音、面板纵向靠上且固定顶部向下伸缩。完整口径与路径证据见本文件决策表、当前计划及自主验收记录。 |

---

## 重签名注意事项（开发期）

每次运行 `build_launcher.sh` 后可能因签名变化导致 Accessibility TCC 记录需要重新确认：
- 脚本不再自动 `tccutil reset`，也不主动打开系统设置，避免构建阶段修改用户权限状态。
- 启动时由现有权限引导流程处理辅助功能授权；必要时在列表中找到 CapsWriter → 关闭再打开，或删除后重启软件重新授权。
- 麦克风权限一般无需重置，除非录音全零才由用户手动执行 `tccutil reset Microphone com.capswriter.client`。

---

## 下一步工作

> **权限引导已于 2026-06-22 重写完成（四文件编译通过，待明日从零实测）**，最终设计见架构决策第六节『权限引导（2026-06-22 重订）』+ 决策表「权限引导（两权限）/ 权限恢复 UX」。调查过程留痕见 [`docs/macos-permission-investigation.md`](docs/macos-permission-investigation.md)，但**其中早期结论已被本轮收敛取代**，勿再据其行动：①健康判据采 **option C「单次启动校验 ping」**，非全程回调心跳，运行期仍用 `CGEventTapIsEnabled`；②`IOHIDCheckAccess` 经核查有官方文档、**不迁** CG API，仅降级为提示；③IM 条目注册靠「AX 就绪后补一次 tap 尝试」（request API 实测无效）；④稳定 DR 签名是**另一条独立根因线**，本轮未做。

| 优先级 | 任务 |
|--------|------|
| P0 | ✅ **非 Caps 快捷键不加载 hidutil remap / F18 bridge**：2026-07-05 已修复并完成最小验证。`CapsWriterClient` 只在 macOS、`macos_caps_mode=remap_f18` 且当前启用快捷键中确实包含 `caps_lock` 时创建 `MacOSCapsRemapSession` / `MacOSCapsF18Bridge`；若用户改用 right ctrl、F12、鼠标侧键等非 Caps 快捷键，不再写入系统级 Caps Lock→F18 映射。验证：`.venv/bin/python -m py_compile core/client/app.py`；纯函数断言覆盖 Caps 启用、Caps 禁用、其它键、鼠标键四类配置。 |
| **P0：权限引导重写** | ✅ **2026-06-24 实测主路通过 + 二次收敛落地**。两权限都引导 + `run_guide` 状态机 + option C 启动校验 ping + 绝不退出进程 + **砍掉手动面板/stale 交给 reset-permissions**。剩余复验项见「实测进度」表 #5–9（重点：菜单栏绿点修复复验、运行中撤 AX 键盘恢复、stale/reset-permissions/perm_phase）。 |
| P0 | 用同一批音频样本对比 `qwen_asr_mlx` 与 Windows `qwen_asr` 路线，区分“量化差异”与“接入差异” |
| P0 | ✅ fork `mlx-qwen3-asr` 已作为根目录子仓库接入，主仓库跟踪 fork 内 `capswriter-macos` 分支；2026-07-06 已补服务端导入路径保险，worker 启动 `qwen_asr_mlx` 时优先加载根目录子仓库源码，实测 package path 为 `/Users/edgar/programs/CapsWriter-Offline/mlx-qwen3-asr/mlx_qwen3_asr/__init__.py`。 |
| P0 | 在正式 ASR 评测前完成配置归属收敛：所有影响 ASR 输出的推理参数集中到 editable `mlx-qwen3-asr` package 内；CapsWriter server 外层只保留传输、队列/调度、后端选择、模型目录解析、用户请求元信息，以及权重常驻/预热这类运行开关或资源预算的透传；server 不负责 generation、prompt、chunking、aligner、wired limit 计算和 MLX 底层调用 |
| P0 | 🟡 第一轮后端调优执行清单：①✅确认 editable / 等价源码加载；②✅增加导入路径检查；③✅在子仓库内提供 CapsWriter 专用 `QwenASRRunner`，而不是裸用 `Session.transcribe()`；④✅server 已接同一 runner，评测 driver 待接；⑤✅统一语义：`task_id` 是完整识别任务标识，不再额外引入 `RecordSession` 层级；⑥✅`qwen_asr_mlx` 按后端分叉，不走旧 `WorkPipeline` 片段拼接，而是把同一 `task_id` 的 `AudioFeedPatch` 按时序 feed 给 runner；⑦🟡runner 当前已支持 package-owned“流式喂音频 + final 离线完整结果”，但尚未实现录音过程中提前处理稳定 `InferenceChunk`；⑧✅其它模型保留现有 60s 分段 + 4s overlap 的旧 Server `Work` 路径；⑨后续只比较 8bit/4bit、auto/forced language、no context/tech context、`max_new_tokens` 截断观测；暂不碰 temperature、draft model、diarization、timestamps、streaming、AGC/EQ/降噪 |
| P0（依赖上一行） | editable 源码加载和 `QwenASRRunner` / 中层编排落地后，在 package 内实施**权重 wiring（`mx.set_wired_limit`）+ 一次性启动预热**；wiring 做成 server 可选项（`Qwen3ASRMLXArgs`，默认开，README 写明可关），由 server 透传到 editable package；具体 wired limit 计算、`mx.set_wired_limit` 调用、预热时机和 MLX 细节全部在 package 内完成。方案见 `docs/macos-architecture-decisions.md` 第九节 |
| P0 | 复核并补齐 `qwen_asr_mlx` 当前缺失能力：服务端热词、解码参数、chunking 策略、aligner 接法 |
| P0 | ✅ 已修复 `Caps` 长按竞态（见看板任务 E）：覆盖“开流已开始但 `task.is_recording` 尚未置真时松手”的 stop 丢失场景，麦克风流必定被关闭。**待真机运行时复验** |
| P1 | 评估默认模型策略是否仍应保持“macOS 优先 8bit”，或改为可配置优先级 / 按机器回退 4bit |
| P1 | 在 fork 路线稳定后，再决定是否需要更下沉的 MLX 层改造；当前不投入产品级流式识别实现 |
| P2 | ✅ 菜单栏下拉菜单已落地（见任务 M8）。**待真机复验**：液态玻璃/深浅色观感、复制/编辑/重启/退出四动作的实际行为 |

---

## 实测进度（权限引导，2026-06-24）

> 前置（模拟从零）：`capswriter reset-permissions`（= 停 client + tccutil reset 两权限），或手动 `tccutil reset Accessibility/ListenEvent com.capswriter.client`。
> **重要教训**：测前务必 `capswriter restart` 让新代码生效——2026-06-24 首测因跑的是旧进程，误见已删掉的「加加减减面板」、通知文案不对，restart 后即正常。

| # | 测项 | 结果 |
|---|------|------|
| 1 | **丝滑首装一次重启**：从零 → 弹 AX 框 → 开 AX 开关 → 程序自动让 IM 条目出现 → 开 IM 开关 → 重启 → 可用 | ✅ 通过 |
| 2 | **option C 启动校验 ping**：合成 F18 ping 流过 HID tap 被回调收到，健康判活、不误判 stale | ✅ 通过 |
| 3 | **引导不弹手动面板 / 不说删条目**：刚注册新条目（开关未开）只提示「打开开关」 | ✅ 通过（restart 新代码后） |
| 4 | **通知文案正确**：撤 AX 后引导能正常提示「✅辅助功能已就绪」→「权限已就绪请重启」 | ✅ 通过 |
| 5 | **运行中撤辅助功能（系统设置拨开关）**：键盘 ~1s 恢复、进程不退出、无 13s 死循环 | 🟡 引导/通知已验，键盘恢复+不退出待再确认 |
| 6 | **菜单栏绿点不灭（任务 G）**：撤权后圆点应转红 | 🔲 **已修代码，待复验** |
| 7 | **stale（重签后）**：`build_launcher.sh` 重签 → start → 应通知「请运行 reset-permissions」 | 🔲 待测 |
| 8 | **reset-permissions 命令**：停 client + 清两权限 + 提示，之后 start 从零重走 | 🔲 待测 |
| 9 | **状态可见**：`perm_phase` 反映 probing/guide_ax/guide_im/ready（**已无 stale_manual**） | 🔲 待测 |
| 10 | **弹窗顺序（任务 I）**：从零启动应**先弹辅助功能窗**，AX 配好后才弹输入监控窗；首弹时 IM 条目不应提前出现 | 🔲 **已修代码，待复验** |

---

## 最近故障记录

### 2026-06-29：SoundSource 等虚拟音频驱动 / 插拔耳机导致录音失效

- 复现时间：2026-06-29。用户安装 SoundSource（Rogue Amoeba，底层 ACE/ARK 虚拟音频驱动，用于均衡浏览器音频听网课）。
- 现象：SoundSource 一开，麦克风即失效、CapsWriter 录不到音；**关掉 SoundSource 也无效，只能重启客户端**才恢复。
- 根因（已修，提交 `dfc50a1`）：`sounddevice`/PortAudio 首次 import 时 `Pa_Initialize` 把整张设备列表与默认输入设备索引一次性缓存，运行期不刷新。SoundSource 的虚拟驱动加载会改变 CoreAudio 设备拓扑（设备增删、默认输入切换），旧缓存里的设备句柄/默认索引失效 → `sd.InputStream(device=None)` 指向错误/失效设备录不到音；缓存只在进程初始化时建立，故关掉干扰软件无效、只有重启进程重走 `Pa_Initialize` 才恢复。**同一根因也会让「插拔耳机/AirPods/USB 麦切换默认输入」后录音不跟随。**
- 对策：`core/client/audio/stream.py` 抽出 `_reload_portaudio()`（terminate → 重新 dlopen → initialize，复用原 `reopen()` 逻辑）；**macOS 下每次 `start()` 建流前先调它刷新设备列表与默认输入**（按需开流模式 start() 必在「无打开流」状态被调用，重载安全）；`reopen()` 改调同一 helper 去重。
- 口径收敛：**不写「按名字匹配/记住上次设备」逻辑**——开流坚持 `device=None`（= 跟随 CoreAudio `kAudioHardwarePropertyDefaultInputDevice`，即系统设置→声音→输入选中项，macOS 会自动为耳机/AirPods 切换）+ 开流前刷新，即「用户在系统设置里选谁就用谁」，最简且最符合直觉。已修复后 SoundSource 开关、插拔耳机均自动跟随，无需重启客户端、无需手动选。
- 代价：每次开流前多一次 PortAudio 重载（几十毫秒量级）。用户实测确认录音成功，**延迟问题暂不优化**（可等菜单栏麦克风胶囊出现后再开口）。

### 2026-08-12：临时推翻「跟随默认输入」口径，固定用 Mac 内建麦克风（用户拍板）

- 背景：`dfc50a1` 后开流跟随系统默认输入设备，但用户**经常戴耳机**，耳机麦克风收音差，默认输入被 macOS 自动切到耳机麦 → 希望固定使用本机内建麦克风。**临时策略**，后续可能再调整。
- 改法（未提交）：`core/client/audio/stream.py` 新增 `_find_builtin_mic()`（按设备名匹配内建麦克风：`内建` / `Built-in` / 含 `麦克风|Microphone` 且含 `MacBook`），`start()` 中 macOS 下优先用它指定的设备索引开流，找不到内建麦时回退 `device=None` 跟随默认；**保留 `_reload_portaudio()`**（刷新设备列表本身无害，且保证插拔后索引变化仍能找到内建麦）；Windows 等其他平台不受影响。控制台/日志打印「使用内建麦克风」。
- 本机验证：`MacBook Air麦克风`(index=0) 正确命中，外接 `EDIFIER X Clip` 不被误匹配。
- **注意**：此改动临时推翻 2026-06-29 条目「口径收敛：不写按名字匹配逻辑」——恢复跟随默认（或改配置项）时删掉 `_find_builtin_mic` 的调用即可。

### 2026-06-24：权限引导实测三个问题（旧进程残留 + 绿点不灭）

- 背景：权限引导 2026-06-22 重写后首次从零实测，并在测中做了 2026-06-24 二次收敛（砍手动面板）。
- 现象与定位：
  1. **拨开关后仍弹「加加减减权限面板」**：该面板文案在所有 `.py` 里已搜不到（确属已删）→ 判定**当时跑的是旧进程**（client 启动时间早于文件 mtime）。用户 `capswriter restart` 加载新代码后，面板消失、通知文案恢复正常。**教训：测前必 restart。**
  2. **只弹「已就绪」不提示重启**：同因旧进程；新代码 `run_guide` 阶段 4 会发「权限已就绪，请重启 CapsWriter」，restart 后验证正常。
  3. **撤辅助功能后键盘接管已失效，菜单栏圆点仍绿**（真 bug，见看板任务 G）：圆点读 `ErrorBus.state`，`result_processor` 只在连接状态变化时重算 state，运行中撤权走 `_handle_tap_failed` 只改了 `accessibility_ok` 没改 `state` → 停在 ready。已修：`_handle_tap_failed` 与 start() stale 分支改为 `eb.update(state='error', ...)`。**待复验。**

### 2026-06-24（续）：从零引导弹窗顺序错乱（输入监控抢先）

- 现象：从零启动 → **输入监控窗立刻弹**，几秒后才导航到辅助功能；AX 配好后又回到输入监控；且第一次弹窗时输入监控条目已在列表里。
- 根因（见看板任务 I）：`bridge.start()` 一上来就 `_listener.start()`→`_create_tap()`，而 `CGEventTapCreate` 本身会触发「输入监控」TCC 弹窗并注册 IM 条目——这一发抢在了 `run_guide` 的 AX 弹窗之前。
- 风险：用户顺手先开了输入监控就重启，辅助功能仍缺 → 行为不可预测。
- 修：`start()` 用只读 `check_accessibility()` 前置判断，AX 未就绪时不预创建 tap，直接进引导（AX 先弹），AX 就绪后才补 tap 尝试注册/弹 IM。**待复验。**

### 2026-06-19：Caps 长按竞态导致麦克风未关闭

- 复现时间：2026-06-19 13:09 左右。
- 用户侧最终现象：松手后系统仍持续显示麦克风开启；重启 client 后恢复。
- 当时仍然正常的部分：`Caps` 接管未丢，client 仍持续写心跳，短按 `Caps Lock` 仍能正常切换大小写。
- 明确异常的部分：麦克风占用未释放，没有走到正常的 stop/close 流程。
- client 关键日志序列：
  - `13:09:55.872` `[caps-controller] hold threshold reached, start recording`
  - `13:09:55.872` `[audio] stream open requested by recording session`
  - `13:09:55.874` `找到音频设备: MacBook Air麦克风, 声道数: 1`
  - `13:09:56.301` `[caps-controller] long press, stop recording`
  - `13:09:56.301` `[caps_lock] 当前未在录音，忽略 stop_press_to_talk`
- 由日志缺失反推的结论：
  - 没有看到本次对应的 `task.is_recording=True` / `录音状态已更新: recording=True`
  - 没有看到 `stream close requested by recording session end`
  - 没有看到 server 侧新增的本次麦克风任务日志
  - 因此可以判断：麦克风流开启动作已经开始，但录音状态尚未正式立起；松手 stop 被丢弃后，既没有正确关闭麦克风，也没有把完整录音送入识别链路
- 当前根因判断：
  - `core/client/shortcut/task.py` 中 `task.launch()` 先执行 `start_recording_session()` 打开音频流，再执行 `task.is_recording = True`
  - `core/client/shortcut/shortcut_manager.py` 中 `stop_press_to_talk()` 只有在 `task.is_recording` 已为真时才会真正 stop
  - 如果用户在“音频流已开始打开，但 `task.is_recording` 还没置真”的竞态窗口里松手，就会出现 stop 被忽略、麦克风未关闭、识别未真正启动的异常
- 后续修复方向：
  - 需要补齐“录音启动中 / 待停止”语义，或者把状态置位时机前移
  - 目标不是只修日志口径，而是保证：**只要麦克风打开动作已经开始，松手后就一定存在可达的关闭路径**

### 2026-06-22：输入监控未更新导致键盘接管 fatal 死循环

- 复现时间：2026-06-22 12:35 前后。
- 触发背景：先修复了 launcher rpath off-by-one（见任务 F 更新），client 已能正常启动；随后用户**仅在系统设置里更新了“辅助功能”，未更新“输入监控”**，重启 client。
- 用户侧最终现象：先提示“键盘接管未生效”，再提示“权限已配置，待重启”，重启后**依然不断循环**，键盘接管始终无法真正建立。
- 关键日志序列（每 ~13s 换一个新 PID：46652 → 46739 → 46817，循环往复）：
  - `[caps-remap] enabling CapsLock -> F18`（已写入 remap，Caps→F18 映射生效）
  - `[f18-listener] CGEventTap 创建失败，请确认已授权辅助功能（Accessibility）权限。`
  - `[caps-f18-bridge] CGEventTap 真故障，恢复键盘并引导用户重授权`
  - `[caps-remap] restoring original UserKeyMapping=[]`（恢复键盘）
  - 进程退出 → launchd `KeepAlive(SuccessfulExit=false)` 重新拉起 → 回到第一步
- 根因判断（用户确认）：
  - **CGEventTap 实际受“输入监控（Input Monitoring）”门控，而非仅“辅助功能”。** 本次只更新了辅助功能、输入监控未更新，所以 tap 创建持续失败。
  - 代码层把 tap 创建失败一律归为 fatal，fatal 路径会让**进程退出**，再被 launchd `KeepAlive` 拉起 → 形成 ~13s 一轮的死循环；用户无法跳出。
  - 日志文案只提“请确认已授权辅助功能”，**误导**用户只去开辅助功能，掩盖了真正缺失的输入监控。
- 与既有决策的冲突：
  - 此前（CLAUDE.md「权限恢复 UX / 决策表」与任务 M7.2/M7.3）已把输入监控**移出**自动引导链路，只在 CLI/通知里作人工提示。本次实测推翻该决策——输入监控是 CGEventTap 的硬门控，必须重新纳入引导。
- 已采取的临时动作：`capswriter stop` 止住循环（已确认无 client 进程）。
- 下一步（见「下一步工作」P0 最优先项）：重做权限引导，重新纳入输入监控门控；并消除 fatal→退出→KeepAlive 重启的死循环（退出前确认权限就绪，或改原地等待不退出）。
- 旁证（另一相关风险，非本次根因）：`build_launcher.sh` 重建会以 **ad-hoc 签名**重签（`Signature=adhoc`，cdhash 随构建变化），辅助功能 / 输入监控的 TCC 授权按 cdhash 绑定，**每次重建都会失效**。重做权限引导时需一并考虑“重签后授权失效”的口径（评估稳定自签名证书以让授权跨重建存活）。

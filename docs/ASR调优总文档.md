# ASR 调优总文档

## 重启后单句高延迟与连续录音乱序（2026-09-19）

### 根因与实际链路证据

本次故障不需要后台文件转录、不需要历史任务、不需要连续录制：**一条录音内部的音频包就会积压**。客户端麦克风优化将macOS采集块从50ms改为20ms，每秒50包；`AudioRecorder`逐块发送，`ws_recv`把每个包提交为一个Worker工作单元。服务端既有`WorkHandler.drain_queue()`在缓冲非空时仍以20ms超时等下一包，持续到包可使收包循环迟迟不返回；松手后每处理一块还会再等20ms，10秒录音约500包，仅这部分等待即可接近10秒。原50ms间隔有足够空隙使循环及时返回，20ms间隔触发了这个隐藏的节拍问题。

另外，旧`WorkBuffer.pop()`通过`next(reversed(...))`优先最新task，与模块声称的同socket FIFO不符。旧句尚未消费完时新句插队，放大等待并让返回顺序颠倒。这是连续录制的额外故障，不是单句高延迟的必要条件。

以下来自9月19日已发生故障的`logs/server_latest.log`，未要求用户重新录音。final处理区间是Runner入口日志至完成日志，包含最终推理与该段适配处理，不是GPU独占耗时：

| 任务短ID | 录音时长 | final提交 | final开始处理 | 结果完成 | final排队 | final处理 |
|---|---:|---|---|---|---:|---:|
| `52d4e04e` | 5.70s | 09:42:13.378 | 09:42:33.173 | 09:42:33.633 | 19.795s | 0.460s |
| `58aebba2`（后录先返回） | 6.88s | 09:42:24.372 | 09:42:30.979 | 09:42:31.906 | 6.607s | 0.927s |
| `65cb36da` | 10.78s | 09:42:50.258 | 09:44:29.685 | 09:44:30.418 | 99.427s | 0.733s |
| `d1994ef6` | 6.52s | 09:45:46.866 | 09:45:53.082 | 09:45:53.708 | 6.216s | 0.626s |

最后一条客户端松手为09:45:46.673，客户端收到结果为09:45:53.710：松手至提交约0.193秒，服务端完成至客户端收到约0.002秒。主要异常发生在Worker调度，而不是网络传输、编辑框或语言前缀/尾块补齐的最终推理区间。最近服务端精度修复保留；不能将历史适配器直调用验收当成完整Worker调度验证。

### Runner重构与“30秒提前推理”的历史核查

本案“旧代码”指9月19日09:36重启后仍在运行的、本轮调度修复前代码，不是回退到最初版本。实际调用链有三层，不能把收包、缓存与模型推理混称为处理：

| 阶段 | 进入Worker的单位 | 何时调用模型 |
|---|---|---|
| 7月6日`76250e2`重构之前 | 外层攒好的语义片段；短句通常一个工作单元 | 收满外层阈值或收到final；当时客户端配置60s段长、4s重叠，阈值68s；Session内部另做≤30s切分 |
| Runner重构后、麦克风优化之前 | 每个50ms音频包进入Worker，由Runner缓存 | Runner收到final才调用Session，之后内部按≤30s分块识别 |
| 本次麦克风优化后 | 每个20ms音频包进入Worker | 推理触发条件未变，但到包间隔撞上既有20ms收包等待，先造成大量排队延迟 |

源码依据：子仓库`25551b0`与当前`QwenASRRunner.feed_audio`都在非final时直接返回None，只有final进入`_finalize_task`→`Session.transcribe`。当前`transcribe.py`中`split_audio_into_chunks`是此调用之后的离线切分，不是录音到第30秒时的提前推理。主仓库`76250e2`当时的CLAUDE看板也明确“尚未实现录音过程中提前处理稳定InferenceChunk”。用户本轮提出了该能力预期与实现的差距，后续需单独收敛实施；本轮修复调度并不等于已补齐提前推理。

重构将工作单元从大段变为传输小包，却没有同步调整调度等待策略，是潜在缺陷的来源；近期50ms→20ms修改使它稳定暴露。前一版短句只承担一次约20ms等包，自然不会产生“500个包各等20ms”的十秒级额外延迟。即使保持final才推理，移除逐包等待也足以消除本案已经证实的额外等待机制。

### 修复与验证入口

- Worker有积压时用`get_nowait()`，仅完全空闲才阻塞等待；每轮最多读取64项，包括失效连接的包，避免持续输入饿死处理。
- 缓冲按socket分组，内部FIFO保留连续录音及其final顺序，不同socket逐工作单元轮转。长文件最终推理本身仍是同步执行，不承诺推理期间可抢占。
- 只有真正清除断连session后才扫描包缓冲，正常逐包处理不反复复制整个积压队列。
- Runner最终日志新增`final排队`与`final处理`，原`耗时`仍为final提交至完成，避免把总延迟误称模型纯推理耗时。
- 无模型调度回归：`.venv/bin/python tools/test_worker_scheduling.py -v`。10项覆盖单条20ms录音、积压逐包等待、连续输入让出处理、同连接录音顺序、跨连接轮转、空闲维护、断连与退出。最初9项在旧代码6项失败；新增单句定时用例在备份旧代码首包延迟10040ms而失败，修复后10项全部通过。
- 麦克风生命周期及数据完整性22项、编辑框结果流与UI契约通过。未新增真实模型复现实验、未重启日常服务；代码验证不等于已验证重启后日常实际延迟。

## 历史范围：同一音频的服务端吞尾差异（2026-09-18）

用户明确纠偏：本轮Mac/Windows同一份既有音频只跑服务端，客户端不在比较路径内。此前采集收尾风险仅是独立旧问题，不能用于解释本次分歧。本轮优先核对声学特征、编码器尾帧长度与补齐、解码EOS决策；保留语言前缀修复。

## 第五轮：定位两处服务端编码器差异（2026-09-18）

### 已证实的卷积尾块错误，已修代码

Windows `inference/encoder.py::_run_frontend` 把不足100帧的最后一块Mel补零到100帧，再经三层stride=2卷积，最后裁回有效长度。Qwen官方 `modeling_qwen3_asr.py:694` 同样先对本条的chunk执行 `pad_sequence` 再卷积，以mask移除无效输出；不足一整块的单条输入只使用自身长度。

MLX `encoder.py::_encode_single` 原来将不足100帧的尾块直接卷积，声称“no extra right-padding”保留等价语义。这在真实非零偏置的多层卷积/GELU下不成立：补齐位置经过前层会产生激活，参与后层最后一个有效token的计算；直接结束短块则把相关中间位置当成边界零值。部分尾长没有差异，部分尾长改变最后一个有效token，随后经注意力传播，影响是否输出尾字。这不是少裁了输出token，也不是客户端丢音频。

- 独立诊断只切换“Mel尾块补齐→卷积→裁回原有效帧数”，不追加PCM静音、不改变prompt/音频token数/生成预算/温度。
- 20条尾缀候选中4条恢复Windows尾字：`20260822-230445` 回应→回应呢；`20260823-005919` 反斜→反斜杠；`20260910-212648` 告诉→告诉我；`20260912-135455` 毕不了→毕不了业。12条语言异常/正常对照均无实质变化。
- 旧语言前缀与新正文前缀分别运行32条/64次，均是相同4条恢复，三条全英文异常在新正文前缀下保持修复效果。
- 修复落在 `mlx-qwen3-asr/mlx_qwen3_asr/encoder.py`：有完整块时尾块补到chunk_size，卷积后裁回ceil(tail_frames/8)；不足一整块保持Qwen行为。未增加有效时长。
- 原测试只与旧MLX逐块循环比较，未校验官方补齐语义，且小模型bias默认0掩盖差异。现基准改为官方pad_sequence语义，设确定非零bias，覆盖37/100/101/107/108/137/199/200/337帧。旧实现3项失败；修复后模型/音频/Runner/Session/提示词/分段/流式等267项测试通过。
- 历史：上游 `497eaa4`（2026-02-16）优化时保留了更早的无补齐尾块，早于CapsWriter Runner重构；不能归为用户7月重构新引入。

### 已实测影响的8秒注意力策略差异，尚未改默认

模型配置及实际解析均为 `n_window=50`、`n_window_infer=800`，对应每8秒/104个音频token一个声学注意力窗口。MLX用block-diagonal mask隔开；Windows ONNX `Qwen3ASRBackendOnnx` 对全部有效token做全局注意力（DML额外补齐仅屏蔽无效token）。这次MLX窗口配置读取正确，符合Qwen原配置；Windows集成采取了不同策略。

仅把MLX窗口mask设为None，不改原PCM、prompt、权重或token数：20条尾缀候选中9条归一化正文与Windows对齐，均超过8秒；包括8.1秒的反斜杠、8.2秒的管你/冲突啊、8.7秒的爱意、8.8秒的别想了。短尾窗口只能在声学编码阶段看到最后少量音频，无法从前窗口获取上下文；文本解码器仍能看到全部编码token，不能说整个模型“只听了最后一点”。此组与卷积组重叠1条，两个单变量分别解释12个不同候选，不能声称组合后必然改善12条。

这属于有证据的调优方向，不能把“与Windows不同”直接等同于违反Qwen原实现。尚未将全局注意力设为生产默认，需在卷积修复+正文前缀基础上验证交互、正常长录音与内存/延迟。

### 其它隔离结果与证据

| 单变量 | 32条中的正文影响 | 对本案的意义 |
|---|---|---|
| Windows NumPy Mel取代MLX Mel | 0条实质变化 | 两者最大元素差约0.00817，但本批未改变正文，不支持它是这批吞尾主因 |
| Windows完整prompt模板 | 3条尾缀候选变化，另3条语言异常及1条英文术语变化 | 提示词也影响末字；其中“正常参加吗”不等于Windows“正常参加”，不能一概视为正确修复 |
| Windows式全局声学注意力 | 9条尾缀与Windows对齐；另1条语言异常恢复 | 与8秒末端窗口高度对应，有明确单变量因果证据 |

复现脚本：`tools/asr_tail_boundary_probe.py`（无参数为卷积隔离，`--text-only`叠加正文前缀，`--secondary`分别检查Mel/prompt/注意力）。记录依次位于 `diagnogs/tail_boundary/20260918-155332/`、`20260918-155453/`、`20260918-155553/`；汇总 `comparison_summary.json`。三批共256次生成全部EOS。旧前缀两批基线64/64复现原Mac输出，新前缀32条基线与第四轮新结果完全一致。官方源码固定在Qwen `7c6daf7`，快照保存于 `diagnogs/default_comparison/20260918/QwenLM/Qwen3-ASR/qwen_asr/core/transformers_backend/modeling_qwen3_asr.py`。

正式代码验收：`diagnogs/tail_boundary/production-fixed/`，通过正式服务端适配器分包跑33条（32条原样本+31.2秒长录音）、两种语言入口，共66次，全部EOS。其中32条×2入口的原始生成token、prompt及格式化正文64/64与诊断尾块修正一致。长录音新正文模式完全不变，旧语言模式仅标点改变。当前两个修复叠加后，20条候选有6条与Windows归一化对齐（语言前缀2条+卷积4条），不代表其余14条都已经排除问题。语法检查、主/子仓库diff检查通过。

本轮不再以采集缺音解释同一既有音频的服务端差异；已找到可复现的具体实现错误和策略差异，但不声称20条全部归因完毕，也未把Windows文本当人工真值。日常服务未重启。

## 当前范围（2026-09-18）

用户已授权修改代码，要求至少完成一项已知问题后停下汇报；由主 Agent 直接完成，不使用子 Agent、不另写 plan。本轮先修复中英混说变全英文。尚无证据证明 Runner 重构导致普遍精度下降，也不能以 Windows 输出作为人工真值。下方前三轮“不改生产”“不新增实验”等限制仅描述当轮历史边界。

## 第四轮：自动语言正文前缀修复（2026-09-18）

### 实现与兼容边界

- `mlx-qwen3-asr/mlx_qwen3_asr/capswriter_runner.py`：集中配置新增 `auto_language_text_only=True`，CapsWriter 默认启用；设为 False 可恢复原自动语言入口。
- `session.py`：同步/异步调用按任务透传，裸 Session 默认仍为 False；不修改共享 tokenizer 状态。
- `tokenizer.py`：仅在未指定语言时预置 `<asr_text>`，保留 system/context、音频占位及聊天消息边界；显式语言优先且只追加一次分隔符。
- `transcribe.py`：所有内部 chunk 使用同一策略；请求时间戳或说话人对齐时保留语言识别入口，避免 unknown 导致 aligner 跳过。正文模式按相邻字符边界拼接，避免中文多空格、英文词黏连。
- 正文模式的 `language` 为 `unknown`，不把语言猜测包装成识别结果；CapsWriter 当前客户端正文输出不依赖该标签。未更改温度、token 预算、模型权重、音频内容或切段算法。
- 吞尾尚未修复：150ms 补静音在上一轮存在「风险/风格」歧义，本轮不启用，也不将另一个旧录音阈值漏包缺陷混称为吞尾根因。

### 验收证据

- 新增 `mlx-qwen3-asr/tests/test_capswriter_text_only.py`；先确认10项调用链测试在旧实现失败、6项拼接测试在缺少对应实现时失败，再验证通过。Runner、Session、tokenizer、transcribe、chunking、streaming 定向测试合计175项通过。
- 正式适配器回归命令：`.venv/bin/python tools/asr_symptom_probe.py --infer --production-regression`。以4096采样点分包，经 `QwenASRMLXEngine.feed_audio_patch` → Runner → Session 运行；只追踪生成器，不用诊断正文前缀覆盖正式实现。
- 记录：`diagnogs/symptom_probe/20260918-151509/` 的 `results.jsonl`、`runtime.json`、`acceptance.json`、`silence.json`。32条录音 × 旧入口/新默认，共64次生成；旧入口32/32复现保存的Mac基线，新入口32/32与前轮正文前缀诊断输出完全一致，全部EOS。同条PCM与生成配置不变，prompt仅增加token 151704。
- 三条全英文异常全部恢复中文+英文术语。9条正常对照中8条无实质变化；英文术语样本 `20260819-091506` 从 `Versailles reacts. Best practice.` 变为 `Versal Reacts Best Practice.`，缺人工真值，不宣称改进或退步。
- 20条尾缀候选仅2条正文改变，均延长尾缀；不能因此宣称吞尾问题已解决。0.1/1/3秒静音在两种入口下共6次生成全部为空，均EOS。
- 追加实际长录音 `20260830-003139`（31.2秒）：正式适配器下两种入口均产生2个内部chunk、全部EOS，最终格式化正文完全一致；记录在 `diagnogs/symptom_probe/20260918-151509-long/`。语法检查和主/子仓库 `git diff --check` 均通过。
- 真实验证覆盖已知样本和有限对照，不代表全9313条准确率，也未覆盖充分的纯英文长句/多语言质量。尚未重启日常服务，现有服务进程未加载本次修改。

## 第三轮：语言前缀与尾部边界定向验证（2026-09-18）

用户在第二轮建议后授权按助手思路继续。此轮只在独立进程通过现有Runner运行定向验证，没有修改生产推理实现、模型参数、原录音或原转录，也没有重启日常服务。第一/二轮关于不新增实验的边界作为当时阶段记录保留。

### 方法与证据

新增诊断入口 `tools/asr_symptom_probe.py`，默认仅生成清单，`--infer`才加载模型；每次输出目录必须不存在。总共32条独立录音：3条全英文异常、中文/英文术语/正常混说各3条对照、20条Mac严格少尾缀候选。纯英文长句对照不足，采用3条至少15个拉丁字母的英文术语；这不代表长英文场景的验证。

- 语言对照：12条 × auto/Chinese/仅正文前缀三路，加20条吞尾原始基线，共56次生成，输出 `diagnogs/symptom_probe/20260918-101038/`。
- 吞尾跟进：20条 × auto/仅正文前缀/末端补150ms静音，共60次生成，输出 `diagnogs/symptom_probe/20260918-101222/`。
- 补静音副作用对照：原12条语言样本 × auto/补150ms静音，共24次生成，输出 `diagnogs/symptom_probe/20260918-101358/`。

全部140次生成完成且为EOS；全部64次基线（包含重复基线）与已保存Mac格式化输出一致。temperature固定0、模型实际198个量化模块均为8bit。同条音频各变体预算固定为原始时长对应值；追加150ms静音不会额外增加预算，所有样本始终小于30秒，不引入切分。检查prompt token：仅正文前缀等于原auto prompt追加151704；Chinese使用原生强制语言；补静音仅改变音频占位数量，不改聊天文本。保存原PCM散列、原始生成token、raw decode、实际配置、language、逐段预算及结束原因。

### 语言异常：前缀干预对3条全部生效

| 样本 | auto | 指定Chinese / 仅正文前缀（两者本例正文相同） |
|---|---|---|
| 20260908-233752 | 后半句全部生成英文 | `Action, schedule, occurrence，这些还有没有有用的元信息？我不是让你去掉元信息，而是精简，并且去重。` |
| 20260911-114212 | `Codex, we're still doing this thing. Directly use subagent.` | `Codex，我们还正在干这件事情，直接用Subagent。` |
| 20260912-110033 | `Mailbox ownership. This is what things are.` | `Mailbox ownership，这都是什么东西啊？` |

这支持“自动语言生成前缀是这3条全英文现象的关键可干预因素”，而不是仅由跨后端关联猜测。但未复原历史依赖，仍不能叫作Runner重构回归；同环境旧Session也能复现auto异常。

正常对照：两种前缀对3条纯中文、3条混说均无格式归一化后的正文变化；3条英文术语中1条发生词形变化：auto为 `Versailles reacts. Best practice.`，Chinese为 `Versal React Best Practice。`，仅正文前缀为 `Versal Reacts Best Practice.`。尚未听音确认哪个更正确，不能把变化直接算改善或退步。另两条英文术语仅有格式差异或不变。

两种方案的区别：指定Chinese仍输出language=Chinese（由强制配置决定，并不证明音频全为中文）；仅正文前缀不生成语言头，当前解析返回unknown。后者没有强制中文，但会失去模型原生自动语言元数据。补150ms静音对3条全英文异常完全无效，不能用尾部补静音替代语言策略。

### 吞尾候选：排除本组预算耗尽，发现声学边界敏感性

20条原始基线全部EOS，生成8–50 token，预算128–344；这组既未撞length，也未触发repetition，因此增加max_new_tokens不是它们的针对性修复。

| 干预（原预算固定） | 20条中正文变化 | 严格增加尾缀 | 格式归一化后与Windows一致 |
|---|---:|---:|---:|
| 仅正文前缀 | 2 | 2 | 2 |
| 末端补150ms静音 | 11 | 11 | 10 |

补静音后可观察到「正常参→正常参加」「告诉→告诉我」「补充文→补充文档」「毕不了→毕不了业」等末尾补全。12条语言样本的补静音对照（3异常+9正常）文本全部原样，格式归一化前也一致。

关键歧义例 `20260814-190645`：原Mac为「会不会改出新的风」，仅正文前缀输出「风格」，补静音输出「风险」，Windows为「风格」。不能把与Windows更一致当作正确真值。11条变化说明解码对尾部声学边界敏感，不能证明所有新增末字在原音频中存在，也不能证明麦克风松键前没有实际漏采。补零不会恢复真实丢失的音素。

### 当前建议与尚未完成

1. 将“仅正文前缀”和“尾部补静音”保留为两个独立候选，不同时切换；前者优先针对混合语言变全英文，后者针对完整音频EOS漏尾。当前未上线任何一个。
2. 在独立诊断入口继续验证比全局强制Chinese更可控；若后续接入产品，应在package集中管理并保留原auto路径，明确仅正文模式的language=unknown，而不伪造自动语言判断。
3. 吞尾下一步最缺的是原音频听辨，而非扩大token上限或提高temperature：先核对上述「风险/风格」及文档/毕业等末字，区分可听到真实尾音与模型补全。真实松键漏采仍需要ADC时间、最终采样点、final先后关系留痕，本轮未在日常客户端安装这种采集诊断。
4. 现有样本不足以证明全局收益，尤其缺正常长英文、纯静音、跨语言类别对照。单点150ms只表明这一取值有作用，尚未比较其他长度或确定最优值；不启动无边界参数扫描。

复现命令（每次自动新建输出目录；需要本机MLX环境）：

```bash
.venv/bin/python tools/asr_symptom_probe.py --infer
.venv/bin/python tools/asr_symptom_probe.py --infer --tail-followup
.venv/bin/python tools/asr_symptom_probe.py --infer --padding-controls
```

验证：脚本语法检查、140次运行记录完整性、基线一致性、预算/温度/前缀与输入样本数约束检查通过。此处验证的是实验执行及可重复输出，不是人工真值上的准确率提升。

## 第二轮：三条链路默认值对照与建议（2026-09-18）

用户最新范围：除重构回归外，同时比较参数与上游默认，提出后续排查及调优建议。仍不预设整体精度下降。本轮只读取源码、核对已有记录及保存官方代码快照；没有修改生产实现、切换模型、重启服务或进行新的模型推理。

### 固定版本与核验依据

- 当前 Runner：子仓库 `25551b0`，基于 `f069a0f`（v0.3.5）。Session、transcribe、generate、tokenizer、audio、chunking 六文件相对基线字节完全一致。
- [官方 Windows CapsWriter](https://github.com/HaujetZhao/CapsWriter-Offline/tree/84912d5218ee5e51e216c54dc15a1cb0f466eb76)：`84912d5`（2026-09-14）。其 Qwen GGUF 适配器、inference/asr.py、encoder.py、llama.py 四文件与本机副本字节完全一致。官方 config 默认 language=auto、context为空、60s/4s 分段也已核验；用户 Custom 当次运行配置未取得，但用户已确认没有改底层推理。
- [MLX 当前上游](https://github.com/moona3k/mlx-qwen3-asr/tree/1e28932dac8e2c68b34c29f32e2be338ffc1c852)：`1e28932`（2026-09-07）。移除函数文档串后 AST 对比：build_prompt_tokens、resolve_max_new_tokens、_detect_repetition、split_audio_into_chunks、log_mel_spectrogram 与本地相同。不能把上游其它性能/线程修复等同于已修复本案精度问题。
- [Qwen 模型团队参考实现](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/qwen_asr/inference/qwen3_asr.py)：`7c6daf7`。用于区分模型原生语义与 Windows 集成默认，不混称两个“官方”。
- 原始快照、版本和散列核验：`diagnogs/default_comparison/20260918/{versions,source_manifest,verified_comparison}.json` 及同目录源码。验证包含 15 项相等检查，全部通过。

### 实際生效的默认值

| 项目 | CapsWriter 当前 MLX Runner | fork 基线 MLX v0.3.5（当前上游相关默认也相同） | 官方 Windows GGUF |
|---|---|---|---|
| 产品语言选择 | auto → None | Session language=None | auto → None |
| 自动语言 assistant 前缀 | 空，让模型生成语言与分隔符 | 相同 | 预置 `<asr_text>`，直接生成正文 |
| 指定 Chinese | 前缀 `language Chinese<asr_text>` | 相同 | 前缀相同，但其余聊天模板仍不同 |
| 空 context 的 system 内容 | 空 | 空 | `You are a helpful assistant.` |
| 聊天边界 | im_end 后有换行 | 相同 | 手工拼装，省去这两处换行 |
| temperature | 0（greedy） | 0 | 0.4 |
| top_k / 随机种子 | greedy 无随机采样 | 相同 | top_k=50，每次随机 seed；top_p=1、min_p=0、惩罚系数默认不生效 |
| max_new_tokens | None → ceil(chunk秒数×12)+32，限制128–512 | 相同；底层 GenerationConfig 的4096不是 Session 实际预算 | 每次解码循环最多512 |
| 重复保护 | 重复 token/短模式检测后直接停止，无自动重试 | 相同 | 最近15个稳定token的种类≤3时熔断，最多4次尝试，每次加温0.3 |
| 内部切分 | ≤30s 原样；更长按低能量点递归切分 | 相同 | CapsWriter 外层到68s阈值后按60s步长、4s重叠；适配器上限80s |
| patch处理 | 16k mono float32逐包缓存，final拼接 | 接收整段数组 | 外层缓存后交模型 |
| 元数据 | return_chunks=True；timestamps=False | return_chunks=False；timestamps=False | 无同等生成结束元数据 contract |
| 预热/内存 | 新增启动静音预热与wired资源预算 | Session本身无同等Runner资源编排 | 编码器自身有预热；与文本策略分开 |

`transcribe.py:694` 显式构造 temperature=0 的 GenerationConfig；RunnerConfig 和 Session.transcribe 均不暴露 temperature。因此只修改模型目录 generation_config.json、或给 CapsWriter server 配置随手添加 temperature，并不能改变当前 MLX 这条实际调用链。若后续试验，需要在 package 内明确接通参数，不散落外层。

Windows 的0.4是 CapsWriter GGUF 集成选择，不代表 Qwen 模型团队推荐的统一最优值。Qwen 参考实现的 vLLM 构造默认 temperature=0.0，其提示词也采用空 system、auto时让模型生成语言信息，与 MLX 更接近。不能把“对齐Windows”直接叫作“恢复Qwen官方默认”。

音频特征方面：两端都为16k、128 mel、400点FFT、160步长、Hann窗、reflect边界、Slaney滤波及相近log压缩；没有发现Runner新增降噪/归一化或重复重采样。但计算实现并非数值一致：GGUF用NumPy/ONNX，MLX用MLX；GGUF先全帧max裁幅再裁最后帧，MLX先裁最后帧再max裁幅，若被裁掉的末帧是最大能量点可产生差异。这是底层差异候选，未验证影响大小，优先级低于提示词；两端都丢弃末端额外STFT帧，不能仅见 `[:, :-1]` 就判定吞音频。

### 两个症状的进一步结论

**混合语言变全英文**：3条明确样本均由模型生成 `language English<asr_text>` 和英文正文；同环境旧Session与Runner一致。优先候选是自动语言生成与文本解码的相互影响，而不是结果处理翻译，原因仍待单变量验证。Qwen官方仓库的[Discussion #27](https://github.com/QwenLM/Qwen3-ASR/discussions/27)有用户在原生PyTorch 1.7B、language=None下输入拼接的中英音频却只得到英文的报告；该报告无维护者回复，且是遗漏中文的类似现象，并非本案翻译机制已获官方确认。

**吞尾**：应独立区分三种服务端停止原因和采集丢失。

1. `eos`：模型自行结束。增加允许生成长度不会强迫它越过EOS；需检查原音频是否已缺尾，或完整尾音下模型仍未转写。
2. `length`：预算耗尽；这是扩大max_new_tokens直接针对的情形。
3. `repetition`：重复保护提前停止，MLX的truncated仍为False。只看truncated=False不足以排除它。Windows这里会重试，MLX直接返回，属于可调策略差异，但已有40条记录均明确为EOS，不能据此认定本案被重复检测切断；它们也不是对所有吞尾候选的专项覆盖。
4. 采集边界：官方Windows同样有recording=false拒收callback、发送create_task不显式汇合，开头阈值缓存漏当前块的旧缺陷也仍存在。Mac增加按需关流，但“回调门控”并非Mac独有或Runner引入。应按采集时间确认松键前的样本是否到达保存/发送/Runner各边界，而不是猜测固定延时能解决。

新定位的可观测性缺口：`QwenMLXRunnerPipeline.process` 只把text/duration等拷入Result，language、chunks、每chunk generated_tokens/max_new_tokens未随结果存档；仅日志写了总体finish_reason/truncated；RecognitionMessage也无对应字段。不是精度根因，却导致现有批跑Markdown无法直接回答全部吞尾案例的结束原因。客户端回调忽略time_info.inputBufferAdcTime，仅保存入队墙钟时间，同样不足以判断松键前物理样本是否完整。

### 推荐的下一步（方案，尚未实施）

| 优先级 | 排查/调优动作 | 控制变量与验收 | 结果意味着什么 |
|---|---|---|---|
| P0 | 增加诊断记录：每任务PCM样本数/散列，每chunk prompt模式、language、generated_tokens/max_new_tokens、finish_reason；采集记录ADC块时间、松键时间及发送final时间 | 优先写旁路诊断记录，普通UI不暴露推理细节；不改变识别参数 | 为吞尾分清采集/传输/生成问题，并让参数试验可归因 |
| P1 | 混合语言小样本对照：A保持auto；B仅指定Chinese；C保持MLX聊天模板/system为空，但assistant仅预置`<asr_text>`（Windows式正文前缀） | 先使用已有3条失败样本，加原本正常的中英混说、纯中文、纯英文对照；固定PCM/模型/量化/temperature=0/预算。每次仅改一项，听原录音确认，不以更像Windows为唯一成功条件 | B/C若减少翻译且保留英文术语，才成为候选；固定Chinese也可能把英文转成中文，不能直接全局上线 |
| P1 | 吞尾专项：先复听已有20条严格尾缀候选的原文件，挑尾音清楚的样本检查原始生成记录；真实松键漏采则补采集时序留痕 | 原文件缺尾与原文件完整分别处理；只有length才单改预算至512，EOS则保留预算检查语言/尾部声学条件；repetition单列 | 避免用模型参数修采集缺陷，也避免因EOS正常结束就认定文本完整 |
| P2 | 若P1指向语言前缀，再分别对照system通用文本、完整Windows模板；之后才对照temperature=0.4/top_k=50 | 完整Windows模板是组合对照，不能单独归因；随机采样须固定并记录seed，再换少量seed核查稳定性 | 判断收益来自前缀、system、模板边界还是采样；不把单次随机改善当稳定提升 |
| P2 | 若完整音频以EOS漏尾，可单独对照末尾补100–200ms静音 | 只作为诊断假设，保留纯静音/短词对照检查幻觉；追加静音不能恢复已丢失真实样本 | 探测结束边界声学条件，不能据未经验证的假设改产品 |
| P3 | 核查历史依赖与8bit/4bit量化及编码器数值差异；评估上游升级 | 前述入口语义固定后再开展；保留现有环境快照 | 当前证据不支持把降量化、全量升级或大改切分作为首选修复 |

限定：C是诊断用新prompt模式，当前传language=None不会自动得到Windows式前缀；也不要传 `Chinese,English` 试图表示混合语言，CapsWriter语言映射不识别这个组合，会返回None。上游输出的逗号连接语言元数据不是这里支持的强制语言输入。

首选顺序是“必要留痕 → 语言前缀单变量 → 吞尾分层定位”；不一起改prompt、temperature、token上限和量化。若仅部分异常样本改善但正常混说/纯英文退步，仍不能作为新默认。任何调优结论都需要原音频核对与对照样本，当前仅完成代码层建议。

## 第一轮代码排查结论（2026-09-18）

### 范围与结论边界

用户 2026-09-18 再次澄清：整体精度是否下降并不确定，可能是使用要求提高；Windows 也存在识别问题。排查不得预设 macOS 整体精度劣化，当前重点仍是审查之前 Runner 重构是否引入代码错误，跨后端文本分歧只作辅助线索。

审查主仓库 `76250e2` 及子仓库 `f069a0f → 25551b0`。用户确认 Windows 批跑来自其 CapsWriter-Offline fork 的 Custom 分支，未改变底层推理管线，可按官方 Windows 路线理解；尚未拿到精确 commit 和运行配置快照。本机 GGUF 源码是路线对照依据，而非已验证逐字一致的 Windows 执行快照。

目前没有锁定一个能解释普通短句普遍退步的 Runner 新增缺陷。跨平台文本分歧已确认，但它不等同于历史回归，也不等同于 Mac 错误率。此次不修改生产推理代码，不做参数调优，不重启日常服务。

### 已定位的代码事实

| 区域 | 代码位置 | 审查结果 |
|---|---|---|
| 生成预算 | `mlx-qwen3-asr/mlx_qwen3_asr/capswriter_runner.py:33,240` | `max_new_tokens=None` 原样保留并传入 Session；按推理 chunk 时长计算 128–512 的预算，没有改成统一固定上限。 |
| PCM 累积 | 同文件 `feed_audio`、`_concat_audio`、`_prepare_audio` | 实际协议 16k mono float32 下仅累积/拼接；未逐 patch 归一化、提特征、裁剪或补零。非 16k 的逐 patch 线性重采样确实不保证与整段重采样等价，但不属于当前服务端协议路径。 |
| Session 调用 | 同文件 `_transcribe_prepared_audio` | final 仍调用既有 Session，语言映射和 context 默认与旧适配器一致；新增 `return_chunks=True` 控制结果元数据，未改变正文生成；没有接入 draft model。 |
| 跨后端提示词 | `core/server/engines/qwen_asr_gguf/inference/asr.py:69` 与 `mlx-qwen3-asr/mlx_qwen3_asr/tokenizer.py:373` | auto 时 GGUF 预置 `<asr_text>`，MLX 留空 assistant 前缀让模型生成语言及正文分隔符；空 context 时 GGUF 使用 `You are a helpful assistant.`，MLX system 内容为空；GGUF 少了 MLX 模板中的两处消息边界换行。均不是这次 Runner 新改的参数。 |
| 跨后端采样 | GGUF `asr_engine.py:44`、`inference/asr.py:128,192`、`inference/llama.py:665`；MLX `generate.py:29` | GGUF 路线默认 temperature=0.4、top_k=50、新随机种子；检测重复后加温重试；MLX 默认 temperature=0，贪心生成。不是同权重名称就代表相同推理过程；尚未证明哪项导致本批分歧。 |
| 长音频编排 | `core/server/connection/ws_recv.py:88,166` | 新 MLX 路线绕过外层 60s 分段、4s overlap 及 WorkPipeline 拼接，整任务交给 Session 约 30s 切分。旧阈值为 68s；本批只有 12 条达到阈值，不能解释其余 2171 条实质差异。 |
| 模型位宽 | 模型张量与 `diagnogs/runner_regression/20260917-220717/runtime.json` | 实际加载的 198 个量化模块全部为 8bit；config/model card 的 4bit 标注不符。Windows 用户报告为 4bit；位宽不同是变量，不能据此判断谁更准。 |
| 独立录音缺陷 | `core/client/audio/recorder.py:142` | 首次跨过录音阈值且缓存非空时，`np.concatenate(self._cache)` 未包含当前 `task['data']`；当前 callback 块既未写盘也未发送，常见约 50ms。Git blame 回溯至 `e80e2181`（2026-01-10），早于 Runner；与这批同一已保存录音的服务端对照无因果对应。本轮仅记录，未修改。 |

收尾追加审查：

- `capswriter_runner.py::_prewarm_safely` 直接调用 Session，不进入 `_states`；`generate.py::generate` 每次调用 `model.create_cache`，没有沿用预热或上一条识别的 KV cache。
- `work_handler.py::WorkBuffer` 同 task_id 用 deque FIFO，单 worker 串行执行；`ws_recv.py` 保留 final 所带 data，`feed_audio` 先追加数据再 finalize。正常连接且 task_id 唯一的路径中，未找到 patch 乱序、尾包被直接丢弃或提前清空任务的代码点。断连异常恢复不据此宣称完全无缺陷。
- 新流水线复用 `TextFormatter`，但绕过旧 `_process_simple_merge` 中删除 `@@` 和压缩空白的清洗；外部 aligner 对 text_accu/时间戳的补齐也被替换为占位。这是实际输出处理差异，不是 prompt、音频特征或生成 token 改变的证据，不能拿来解释普通短句词语识别退步。

### 2183 条实质差异主要是什么

原始 Markdown 再次全量复核：9313 对，完全一致 5499、仅格式不同 1631、实质差异 2183。规则：`（空）` 归空、小写化、移除 Unicode 标点/符号/分隔符和空白。以下用 `difflib.SequenceMatcher(autojunk=False)` 对齐归一化后的字符串，相似度为 `2 × 匹配字符数 / 两串总字符数`；它不是 CER 或准确率。不同维度不能相加。

| 观察维度 | 数量 | 占实质差异 |
|---|---:|---:|
| 字符相似度 ≥90% | 1708 | 78.2% |
| 字符相似度 70%–90% | 384 | 17.6% |
| 字符相似度 <70% | 91 | 4.2% |
| 只有一处连续差异区 | 1485 | 68.0% |
| 差异字符不含拉丁字母/数字 | 1577 | 72.2% |
| 差异字符包含拉丁字母/数字 | 606 | 27.8% |

主体是局部词字选择，包括同音词、代词、语气词及专业术语。字符对齐中的高频替换有 Mac「他」/Windows「它」139 次、Mac「唉」/Windows「哎」47 次、Mac「会话」/Windows「绘画」28 次。这是差异片段出现次数，不能全部计为 Mac 错误；部分差异单靠声音也不能唯一判定，技术语境下也存在 Windows 更不合理的输出。

中英混合的显著异常有明确样本，但未占多数。按「Mac 无汉字且至少 15 个拉丁字母、Windows 至少 8 个汉字」保守筛出 3 条、反向为 0 条（此规则不是所有语言切换的完备检测）：

- `20260908-233752`：Windows 为「Action, schedule, occurrence，这些还有没有有用的元信息？……」，Mac 后半段输出为英文「these. Have. Any useful original information? ...」。
- `20260911-114212`：Windows 为「Codex，我们还正在干这件事情。直接用 software agent。」，Mac 为「Codex, we're still doing this thing. Directly use subagent.」。
- `20260912-110033`：Windows 为「Mailbox ownership，这都是什么东西啊？」，Mac 为「Mailbox ownership. This is what things are.」。

这些文本呈现疑似翻译或语言选择偏离；未据人工听音真值判错。提示词/语言前缀差异与此类现象机制上相关，但现阶段没有因果验证，不能写成已锁定根因。

关于句尾：非空归一化文本严格前缀比较下，Mac 少尾缀 20 条、Windows 少尾缀 27 条；其中既有「文/文档」「毕不了/毕不了业」等词尾差异，也有语气词/重复尾字。因此既不能把所有尾缀差异叫作音频丢失，也不能据此排除其他不满足严格前缀关系的漏尾现象。

关于空结果：Mac 独有空 16 条，其中 Windows 有 13 条仅「哎/嗯/啊」，另 3 条为「Yeah.」「没事。」「好，这个问题。」。不是大规模整句消失。

超过 68s 的 12 条虽然均有实质差异，但两端相似度全部 >95%。长句更容易至少出现一处差异，不能把长音频的逐条不一致率当作整体字错误率。这里没有证据证明新切分更差。

### 用户明确的两个独立症状（2026-09-18）

用户指出「吞尾部」「中英混说变全英文」可能真实存在。它们不依赖“整体精度已退步”这一前提，必须分别审查；不能因发生次数少或尾缀差异双向存在而排除。

**中英混说变全英文：已定位到生成阶段，未定位到 Runner 回归。** 3 条样本的已有原始 token 记录都以 `11528, 6364, 151704` 开始，对应 `language English<asr_text>`，后面为英文正文。旧 Session、Runner 整段、Runner 50ms 三路 token 完全相同：`20260908-233752` 为 36/220 token，`20260911-114212` 为 19/128，`20260912-110033` 为 13/128，均 EOS、未截断。源码及原始 token 表明它不是最后格式化/拼接把中文翻译为英文。当前自动语言条件下模型生成了这一输出；尚不能把语言标记本身写成已经证实的原因，也不能把同环境复现旧调用等同于历史运行环境复原。

**吞尾部：锁定采集结束边界风险，尚未确认具体病例的根因。** `core/client/shortcut/task.py::finish` 先 `state.stop_recording()` 再关输入流；`core/client/audio/stream.py::_audio_callback` 在 recording=false 时直接 return，后续 callback 即使含有松键前采集的内容也不交付；按需模式关闭活跃流未设置按采集时间排空尾部的机制。blocksize 为 50ms，但实际受设备缓冲与回调延迟影响，不能据此宣称丢失量固定或上限必为 50ms。`d9e79d4`（2026-08-23）新增 abort 是为解决句柄泄漏；本机 sounddevice 的 `close()` 文档也明确 active stream 的 pending buffers 会被丢弃，因此不能把新增 abort 单独定性为新吞尾回归。recording 门控与按需关闭均早于 Runner。相同已保存录音的服务端对照不能看到未保存进去的尾音。

客户端 `recorder.py` 对发送使用 `asyncio.create_task` 而没有显式等待全部发送完成再 final，保留为收尾契约的审查点；当前 WebSocket 发送实现和已有服务端记录尚未提供 final 超车的证据，不把缺少显式屏障直接宣称为已复现乱序。

### 验证记录与尚未回答的问题

- 本轮只审查代码与已有转录文本，没有新增模型推理。统计已从原始两端 Markdown 独立全量复核；画像及可回查例子保存在 `diagnogs/runner_regression/text_difference_profile.json`。
- 前轮留下的 32+8 条运行记录显示：同一现有权重和依赖下，重建的旧 Session 调用、Runner 整段和 Runner 小 patch 路径，PCM/特征/prompt/生成 token/文本均一致，且均无 length 截断、与已保存 Mac 输出一致。它们仅支持当前环境调用等价，不能证明早期真实运行环境与现在相同；也未覆盖旧服务端长音频拼接。
- 未确认：历史 MLX 依赖与实际加载包是否漂移、Windows 当次精确执行快照、跨后端提示词/采样/编码器/量化各自对差异的贡献。没有人工真值，不能给出两端总体准确率排名。
- 第一轮阶段汇报后停下；没有凭跨后端分歧直接改 MLX prompt、强制中文或改采样参数。

## 文档目的

本文档记录 CapsWriter for macOS 当前阶段的 ASR 调优总口径、第一轮评测数据集组合方案，以及首要需要解决的问题。

当前调优目标不是泛化评测所有 ASR 能力，而是围绕 CapsWriter 的真实产品场景建立可重复的评测闭环：

- 中英文语音输入。
- 技术口述、开发者术语、会议式表达。
- 便携电脑端小声说话、低语、收音不理想的使用场景。
- 本机 16G 内存可承受的短周期验证。

## 已收敛口径

### 不自录数据集

第一轮不要求用户自录数据集。数据来源优先使用公开数据集、固定抽样和极少量可复现派生样本。

原因：

- 用户当前没有稳定、系统化收集自录数据的条件。
- 评测集需要先可复现，避免每轮调优的样本来源变化。
- 公开数据足够支撑第一轮基线判断。

### 不改当前产品预处理链路

第一轮调优不新增响度归一化、AGC、降噪、EQ、混响补偿等产品预处理。

当前管线事实：

- 客户端录音拿到 `sounddevice.InputStream(dtype="float32")` 原始输入。
- 发送服务端前做 48kHz 到 16kHz 的简单抽样和通道平均。
- 服务端把 `bytes` 转回 `float32` 后直接交给 ASR 引擎。
- `qwen_asr_mlx` 适配层只做 `float32` 化和必要重采样。
- `mlx-qwen3-asr` 的音频加载只做单声道、采样率、PCM 范围转换和 Whisper 风格 log-mel 特征，不做 RMS/LUFS/AGC 级响度归一化。

因此第一轮评测目标是在现有管线下观察模型和接入策略的真实表现，而不是先改变输入处理。

### 评测必须走 CapsWriter 推理后端

评测驱动不能直接调用 `mlx_qwen3_asr.transcribe()` 或子仓库自带 benchmark 入口作为主结果来源。

原因：

- 真实产品路径是客户端为一次录音或一次文件转写生成一个 `task_id`，并持续发送带有同一 `task_id` 的 `AudioMessage`。
- 当前旧服务端路径会把同一个 `task_id` 下的连续音频切成多个代码层 `Work` 对象，再由 `WorkPipeline` 调用 `QwenASRMLXEngine`，最后进入 `mlx-qwen3-asr`。
- 如果评测绕过 `QwenASRMLXEngine`，就会漏掉 CapsWriter 当前实际使用的语言映射、context 透传、音频采样率整理、结果格式化和性能元数据。
- 调优目标是改善 CapsWriter 的实际输入体验，不是单独评估上游库裸 API。

第一轮评测驱动应优先做到：

- 在 runner 重构落地前，读取 manifest 音频后可构造与旧服务端一致的 `Work` 作为临时基线。
- 在 Qwen3-ASR runner 落地后，评测主路径必须以同一个 `task_id` 调用 package runner，而不是绕回旧服务端 `Work` 分片。
- 保存 raw ASR 输出、最终格式化输出、耗时、RTF、`finish_reason`、`truncated`、语言配置、context 配置和模型路径。

首轮不强制走 GUI、WebSocket 或真实麦克风录音链路。这样可以避免把客户端权限、网络连接、前台窗口和快捷键问题混入 ASR 质量评估。

### MLX 后端配置归属

当前 `mlx-qwen3-asr` 已作为本项目根目录子仓库接入，`requirements-server.txt` 在 macOS 上从本地子仓库安装该包。后续推理调优应基于这个可编辑源码路线继续推进，而不是继续把上游包当作完全黑盒。

配置归属原则：

- CapsWriter 外层只保留产品级选择：启用哪个 ASR 后端、模型目录解析、客户端传入的 `language` 和 `context`。
- `mlx-qwen3-asr` 包内承载推理级配置：prompt 组装、语言强制策略、generation config、`max_new_tokens` 自适应策略、预热、MLX wired memory、chunking、aligner 接法等。
- 不在 CapsWriter 外层和 `mlx-qwen3-asr` 包内重复维护同一类推理参数。
- 如果某个参数只是为了调优 `mlx-qwen3-asr` 的推理行为，应优先在子仓库内形成集中配置，再由 CapsWriter 适配层传入一个明确配置对象或保持默认。
- 权重常驻、启动预热这类运行配置可以由 server 作为开关或资源预算透传给 editable package，但实现必须在 package 内部完成，由 package 调用 `mlx.core` 等底层接口；server 不直接调用 MLX 底层 API，也不复制 package 内部策略。

因此，在正式评测大矩阵之前，必须先跑通“CapsWriter 使用本地子仓库源码包”的后端路径，并完成第一轮配置归属收敛。否则评测结果可能对应的是旧黑盒接法，后续迁移源码后又需要重跑。

进一步明确：

- 所有会影响 ASR 推理输出的参数，统一集中在 editable `mlx-qwen3-asr` package 内。
- CapsWriter server 端调用实际推理引擎时，不再负责控制模型推理参数。
- server 端可以传入音频和请求元信息，例如来源、语言选择、用户 context、task id；但不应在 server 外层决定 generation、prompt、chunking、aligner、预热、wired memory 等推理策略。
- 对权重常驻等非文本输出参数，server 只负责传入“启用/禁用、预算上限”等运行意图；具体如何计算 wired limit、何时预热、如何调用 MLX，归 editable package 的 runner 或中层编排层所有。
- 如果某个现有 server/client 配置会改变识别文本结果，例如长录音分段长度、overlap、拼接策略，它也应被视为 ASR 管线策略，后续需要迁入 package 或由 package 暴露统一实现，供 server 与评测 driver 共同调用。

### 当前音频与长录音层级

当前产品链路分为几层。

1. 客户端采集与传输

- 麦克风路径：客户端从 `sounddevice` 获取设备原始 `float32` 音频，当前实际按 48kHz 输入处理。
- 发送服务端前，客户端用 `data[::3]` 简单抽样到 16kHz，并对多声道取均值，形成 16kHz、mono、float32 bytes。
- 文件路径：客户端通过 FFmpeg 输出 16kHz、mono、float32 PCM，再按字节流发给服务端。
- 这一层属于产品输入和传输，不属于模型推理参数；但它会影响音频质量，评测时必须复现或明确绕过。

2. 服务端 WebSocket 接收与产品级分段

- `AudioMessage` 携带 `seg_duration` 和 `seg_overlap`，当前默认麦克风和文件都是 60 秒分段、4 秒重叠。
- 服务端 `ws_recv` 按 `seg_duration + seg_overlap * 2` 作为提交阈值，达到阈值后提交一个 `seg_duration + seg_overlap` 长度的 `Work`，再按 `seg_duration` 前进。
- 因此当前典型片段是 64 秒音频，步长 60 秒，片段之间有 4 秒重叠。
- 录音或文件结束时，服务端把剩余缓存作为最终片段提交。

3. 服务端任务处理与结果拼接

- `process_audio_work()` 只把当前代码中的 `Work.data` 从 bytes 转成 `np.float32`，并统计时长；不做 AGC、响度归一化、降噪或 EQ。
- `WorkPipeline` 为每个片段创建 `RecognitionStream`，调用 `QwenASRMLXEngine.decode_stream()`。
- 每个片段的 raw 文本由 `merge_by_text()` 跨片段拼接。
- 如果有 token/timestamp，则用 `merge_tokens_by_sequence_matcher()` 做时间戳路径拼接。
- 最终阶段再走 `TextFormatter` 格式化。

4. `mlx-qwen3-asr` package 内部推理分段

- package 可以直接接收 `np.ndarray` 或 `(np.ndarray, sample_rate)`，会转换为 16kHz mono float32。
- package 内部还有自己的长音频分段：`split_audio_into_chunks()` 默认把超过 30 秒的音频按低能量点递归切成更短 chunk。
- 因此当前 CapsWriter 的一个 64 秒服务端片段，进入 package 后还会被 package 再切成约 30 秒级别的内部 chunk。
- package 内部会把这些内部 chunk 的文本合并为单个片段结果，然后交回 CapsWriter 的 `WorkPipeline` 做产品级跨片段拼接。

### `task_id` 与 `Work` 语义收敛

当前代码里的命名存在一处历史混用，需要在 Qwen3-ASR 重构前先明确。

已经确认的事实：

- `AudioMessage.task_id` 是客户端一次按键录音或一次文件转写生成的稳定 ID。
- 同一个完整音频任务在客户端到服务端的传输过程中，会产生多个 `AudioMessage`，但它们共享同一个 `task_id`。
- `WorkerState.sessions` 以 `task_id` 为键保存 `RecognitionSession`，说明 worker 侧也把 `task_id` 当作完整识别会话标识。
- 当前代码中的 `Work` 类不是完整任务，而是旧服务端按 60 秒分段、4 秒 overlap 切出来的 worker 执行单元。
- 因此当前旧链路实际结构是：一个 `task_id` / 一个完整识别任务 / 一个 `RecognitionSession`，下面可以有多个代码层 `Work` 分片。

后续统一口径：

- `task_id` 就是完整识别任务标识，也等价于当前讨论中的 record session / recognition session 标识。
- 不再额外引入 `RecordSession` 作为新的业务层级，避免把同一层含义拆成两个名字。
- 旧后端仍可在一个 `task_id` 下产生多个 `Work`，以保留现有 60 秒分段、4 秒 overlap、跨片段拼接能力。
- Qwen3-ASR 新路径不再使用旧 `Work` 表达 ASR 语义切片；一个 `task_id` 对应一个 package runner 生命周期。
- Qwen3-ASR runner 内部约 30 秒级别、真正送入模型的单位命名为 `InferenceChunk`，由 runner 自己负责切分、推理和拼接。

命名层级固定如下：

- `AudioMessage`：客户端到服务端的 WebSocket 协议消息，包含 `task_id`、音频数据、`is_final`、语言和 context 等字段。
- `task_id`：一次完整录音或一次文件转写的唯一标识；它就是完整识别任务标识，也就是 record session / recognition session 标识。
- `Work` / `RecognitionWork`：旧后端的 worker 执行单元；当前主代码已经改用 `Work` 命名，避免和 `task_id` 混淆。
- `AudioFeedPatch`：Qwen3-ASR 新路径中，server/worker 按时间顺序喂给 `QwenASRMLXEngine` / package runner 的内部音频增量。它不是 WebSocket 包，也不是推理 chunk；它只表达同一个 `task_id` 下新增的一小段连续音频、时间顺序信息、offset 或 sample 游标、以及 final 标记。
- `InferenceChunk`：package runner 内部拼到稳定边界后，真正送入 Qwen3-ASR 模型推理的约 30 秒级单位。
- `QwenASRRunner`：editable package 内 CapsWriter 专用 runner，负责按 `task_id` 管理音频缓冲、`AudioFeedPatch` 拼接、`InferenceChunk` 切分、推理、结果拼接、`finish_reason` / `truncated` 等元数据收集，并在 final 后返回该 `task_id` 对应的完整结果。实现时优先复用 package 现有的 `split_audio_into_chunks()`、内部 transcribe 编排和结果拼接逻辑，不在 server 侧重写这些策略。

推荐结构：

```text
AudioMessage(task_id, data, is_final, ...)
  └─ task_id = 完整识别任务 / recognition session
       ├─ 旧后端：多个 Work
       └─ Qwen3-ASR：多个 AudioFeedPatch（按时序 feed）
            └─ QwenASRRunner
                 └─ 多个 InferenceChunk
```

这个方案是当前旧代码现状下的最小重构路径：保留跨 client/server/worker 已经存在的 `task_id` 协议字段，只修正服务端内部执行单元命名和 Qwen3-ASR 的切分归属。

### 评测 driver 与产品链路对齐原则

评测 driver 不能直接调用低层 `Session.transcribe()` 来代表产品结果。

原因：

- 低层调用会绕过 CapsWriter 当前 60 秒分段、4 秒 overlap、跨片段文本拼接、最终格式化和服务端任务状态。
- 低层调用会只使用 package 内部 30 秒 energy chunking，这与真实产品长录音路径不一致。
- 如果 driver 把整段长音频直接交给 package，结果可能优于或劣于产品实际结果，但无法解释到真实用户体验。

合理目标应改为：

- editable package 内提供 CapsWriter 专用推理实例或 runner，集中持有所有推理参数和推理策略。
- 当 server 配置 `qwen_asr_mlx` 后端时，链路在服务端识别调度处按后端分叉：不再走旧 `Work` 切分和旧 `WorkPipeline` 跨片段拼接，而是把同一个 `task_id` 下的 `AudioFeedPatch` 按时序 feed 给 package runner。
- CapsWriter server 调用这个 package runner，不在外层改写推理参数，也不负责推理级切分和结果拼接。
- 评测 driver 也调用同一个 package runner。
- 对于短音频评测，driver 可以直接把 16kHz mono float32 或 `(audio, sample_rate)` 传给 runner。
- 对于长录音评测，质量评测可以一次性传入完整音频；时延评测需要模拟 server 向 runner 持续 feed 音频，但切分和拼接仍由同一个 runner 负责。

因此，“driver 直接运行 editable package 推理实例”是合理的，但前提是这个实例不是裸 `Session.transcribe()`，而是 CapsWriter 和评测共同使用的同一层 package runner。若只是裸调 package 低层 API，则会破坏评测与实际使用链路的一致性。

### 不做 Qwen3-ASR 流式推理

当前 ASR 调优只针对 Qwen3-ASR / `qwen_asr_mlx` 这条模型路线，不把 streaming 作为目标。

明确约定：

- CapsWriter 专用 package runner 不实现产品级 streaming 文本输出。
- `mlx-qwen3-asr` 上游 package 虽然提供了 `init_streaming`、`feed_audio`、`finalize_streaming` 等伪流式能力，本轮明确不用这条路径。
- runner 以 `task_id` 为完整任务生命周期，可以接收 server 持续喂入的音频数据；这只是内部音频 feed，不是产品级流式识别输出。
- 语义切段、overlap、prompt、language、generation、结果拼接、`finish_reason`、`truncated` 等推理管线逻辑，都从 runner 开始并由 runner 统一拥有。
- Server 可以继续保留现有客户端到服务端的传输分包、WebSocket、队列、buffer 等机制，但这些只属于 I/O 和传输层。
- Server 不应再把每个传输包当作 ASR 语义片段调用模型。
- Server 现有历史上的流式、实时回显或分片处理代码，本阶段不主动改动；如果未来要做 Server 流式体验，那是独立课题，不纳入本轮 Qwen3-ASR 推理调优。

第零阶段不采用上游伪流式，但也不能简单牺牲长录音的提前计算能力。

需要区分两种实现形态：

1. 简单完整录音后推理。
   - Server 收完一次录音后，把完整音频交给 runner。
   - 优点是实现最简单，评测最容易对齐。
   - 缺点是超过约 68 秒的长录音会失去旧链路“录音未结束就开始推理”的能力，长录音最终结果时延代价偏大。

2. package-owned 流式喂音频 + 离线最终结果。
   - Server 仍然按时间顺序把音频增量送入 worker/package；进入 Qwen3-ASR runner 前的内部增量统一命名为 `AudioFeedPatch`。
   - `AudioFeedPatch` 只作为 I/O 增量数据，不作为 ASR 语义片段，不触发旧 `WorkPipeline` 的跨片段拼接逻辑。
   - Server 和 runner 之间可以是流式音频传输；这只是“喂音频”的流式，不是 ASR streaming 输出。
   - runner 维护同一次录音任务的音频缓冲和内部处理游标。
   - runner 自己决定哪些内部 chunk 已经稳定、可以提前推理。例如录音达到约 30 秒后，runner 就可以开始处理第一段稳定 chunk，而不必等到 Server 原来的 68 秒阈值。
   - runner 不输出实时 partial，不接上游 `init_streaming/feed_audio/finalize_streaming` 伪流式路径。
   - final 到达后，runner 完成剩余音频推理和最终拼接，只返回完整结果。

推荐方向是第二种：package-owned 流式喂音频 + 离线最终结果。

这样可以同时满足：

- Server 不再拥有 ASR 语义切段和拼接策略。
- 语义 chunking、overlap、prompt、language、generation、拼接仍统一在 package runner 内。
- 长录音可以保留“录音过程中提前处理已稳定音频”的性能优势。
- 评测 driver 可以调用同一个 runner；质量评测可直接传完整音频，时延评测可模拟分包 feed。

实现边界补充：

- 其它 ASR 后端继续保留当前 Server 架构：客户端持续发音频，Server 按 60 秒分段、4 秒 overlap 提交多个 `Work` 执行单元，再由 `WorkPipeline` 拼接。
- `qwen_asr_mlx` 单独新增代码路径：Server 不再把中间传输包转换成多个 ASR 语义片段，也不再通过旧 `WorkPipeline` 进行片段推理和拼接；中间音频增量转换为 `AudioFeedPatch`，持续喂给 package runner 的同一个 `task_id` 生命周期。
- 对 `qwen_asr_mlx` 来说，客户端一次按键录音或一次文件转录就是一个完整 ASR 任务；这个完整任务可由多个传输分包组成，但只有 runner 可以决定推理级切段和提前计算时机。
- 这条路径下，音频进入 `QwenASRMLXEngine` 后交给 package runner；正式推理、推理级切段、overlap、prompt、language、generation 和拼接全部由 runner 管理。
- 这不是删除旧 Server 分片机制，而是按后端分叉：保留旧模型所需的 Server 分段，同时让 Qwen3-ASR 路线实现“完整任务进入 runner”的新口径。

### 现有非推理优化与速度影响

CapsWriter 原有链路在推理前已经做了一些非模型层优化。

客户端侧：

- 麦克风输入流按 50ms block 回调，只在录音状态下把音频放入异步队列。
- 录音超过快捷键触发阈值后，客户端边录边把音频通过 WebSocket 发给 Server。
- 发送前客户端把 48kHz 输入用 `data[::3]` 简单抽样到 16kHz，并把多声道平均成 mono。
- WebSocket 传输的是 16kHz mono float32 bytes 的 base64 编码。

Server 侧：

- `ws_recv` 边接收边缓存音频 bytes。
- 对现有后端，Server 默认按 60 秒分段、4 秒 overlap 形成代码层 `Work`。
- 实际提交阈值是 `seg_duration + seg_overlap * 2`，默认约 68 秒；提交片段长度是 `seg_duration + seg_overlap`，默认约 64 秒；步长 60 秒。
- 因此长录音或长文件可以在接收过程中逐段进入 worker 推理。
- `WorkPipeline` 负责跨片段文本拼接、可选 token/timestamp 拼接和最终格式化。

速度影响判断：

- 对普通短按录音，影响很小。原因是短录音通常不到 68 秒，旧链路本来也不会在 final 前提交中间 ASR 片段；改成 Qwen3-ASR 单个 `task_id` runner 生命周期后，仍然是松手后完成最终结果。
- 对超过约 68 秒的长麦克风录音，如果采用“完整录音结束后再推理”的简单实现，Qwen3-ASR 会失去旧 Server 的“边录边提交 ASR 片段”能力，最终结果时延代价偏大。
- 对长文件转录，如果采用简单完整任务后推理，也会失去“边传文件边解码”的流水线重叠，总体 wall-clock 可能变长。
- 因此后续实现应优先考虑 package-owned 流式喂音频 + 离线最终结果：不输出流式文本，但允许 runner 在录音/传输过程中提前处理已经稳定的内部 chunk。
- 但新路径会减少外层 60 秒分段 + overlap + 跨片段拼接带来的重复推理和拼接误差，长音频质量归因会更清晰。
- Qwen3-ASR package 内部仍会做自己的推理级 chunking，因此完整录音进入 runner 不等于模型一次性吃完整长音频。

本轮取舍：

- 当前 Qwen3-ASR 调优优先保证推理链路一致性、评测可复现和参数 owner 集中。
- 不把产品级流式文本输出作为本轮目标。
- Qwen3-ASR 长录音的提前计算应在 package runner 层实现：server 只持续 feed 同一个 `task_id` 的音频，runner 自己决定内部 `InferenceChunk` 的稳定边界和处理时机。
- 若后续确实需要 Qwen3-ASR 实时 partial 体验，应作为独立课题重新设计，而不是复用上游伪流式或恢复 Server 语义分段。

### 子仓库代码评估

`mlx-qwen3-asr` 的代码整体可以作为后续调优基础，但当前 CapsWriter 接入方式还停留在最小适配层。

已确认的优点：

- 上游库有明确的 `Session` API，模型和 tokenizer 生命周期集中在一个对象里，适合服务端常驻进程。
- 推理入口参数集中在 `Session.transcribe()` / `transcribe()` 一层，包含 `context`、`language`、`return_timestamps`、`max_new_tokens`、`draft_model`、`num_draft_tokens`、`diarize`、`forced_aligner`、`return_chunks`、`verbose`。
- 生成配置集中在 `GenerationConfig`，当前默认是 `temperature=0.0` 的确定性解码，并有 `finish_reason`、`truncated` 等可观测字段。
- `max_new_tokens` 有按音频时长自适应的默认策略，可以先作为基线观察，不必第一轮手写 token 上限。
- 子仓库内已有 benchmark、manifest、质量门禁等材料，适合借鉴指标和报告形态。

当前接入的不足：

- CapsWriter 的 `QwenASRMLXEngine` 只暴露 `model`、`return_timestamps`、`max_new_tokens`、`verbose`，还没有接管上游中层推理编排。
- `context` 和 `language` 虽然已从客户端任务透传，但策略仍是“直接传入”，没有 CapsWriter 场景化的 prompt/context 构造层。
- 服务端当前 `.venv` 中导入的包路径是 `site-packages/mlx_qwen3_asr` 安装副本，不是直接指向根目录子仓库源码；因此修改子仓库源码后是否立刻生效还需要先通过 editable install 或等价机制收敛。
- 上游自带 benchmark 主要评估 `mlx-qwen3-asr` 裸库能力，不能直接代表 CapsWriter 实际管线质量。

第一轮结论：

- 子仓库代码质量足够继续投入，不需要另起炉灶。
- 但第一轮正式评测前，必须先把服务端实际加载路径改成可验证的本地源码路径。
- 后续调优应在 `mlx-qwen3-asr` 包内形成 CapsWriter 专用中层编排，而不是继续把所有策略堆在 `core/server/engines/qwen_asr_mlx/asr_engine.py` 适配层。

### 第一轮调优范围

第一轮只纳入最有解释力、最可能影响 CapsWriter 体验的少数调优点。

必须纳入：

- 模型规格：`1.7B-8bit` 与 `1.7B-4bit` 的质量和耗时对比。
- 语言策略：`auto` 与按样本强制 `Chinese` / `English` 的差异。
- context 策略：无 context 与技术场景 context 的差异。
- token 预算观测：记录 `max_new_tokens` 实际配置、`finish_reason` 和 `truncated`，判断是否存在短句/低语/技术词被截断。
- 性能指标：记录耗时、RTF、失败样本、模型加载路径和子仓库 commit。

第一轮暂不纳入：

- `temperature` 搜索。当前默认确定性解码更适合做稳定基线。
- speculative decoding / `draft_model`。它主要影响速度和复杂度，第一轮不应混入质量评估。
- diarization、forced aligner、timestamps。当前产品语音输入主链路不依赖这些能力，第一轮不应扩展变量。
- 产品级 streaming。当前 CapsWriter 首要目标是松手后最终文本质量，不先做流式质量调优。
- EQ、AGC、降噪、混响和复杂麦克风仿真。当前阶段不改预处理链路。
- 上游模型结构配置。`config.py` 中的 encoder/decoder 结构参数不是本阶段产品调优入口。

第一轮需要重点观察但不急于修改：

- 低语和低增益样本是否明显触发 `length`、空输出、重复输出或语言误判。
- context 是否改善技术关键词，同时是否引入幻觉或过度纠错。
- 强制语言是否改善中英文单语样本，同时是否伤害中英混合术语。
- 后处理格式化是否掩盖 raw ASR 错误；第一轮报告必须同时保存 raw 文本和最终文本。

### 两个维度正交组织

评测集按两个正交维度组织，不把内容场景和声学条件混为一谈。

| 维度 | 覆盖内容 |
|------|----------|
| 内容场景 | 中文技术讲解、英文技术词汇、英文技术演讲、商业技术口语、中英混合术语 |
| 声学条件 | 正常音量、真实低语、小声/低增益 |

公开数据集很难天然同时满足“真实低语 + 技术内容”。第一轮接受不同数据源分别覆盖内容场景和声学条件，通过统一 manifest 标注来源、语言、场景和声学条件。

### 低语和小声是核心条件

低语、小声说话不是边缘鲁棒性样本，而是 CapsWriter 便携电脑场景的核心评测条件。

原因：

- 用户经常在办公室、会议室、公共空间中压低音量使用。
- 电脑麦克风收音距离和环境噪声不可控。
- 真实低语的发声机制不同于正常说话，不能只靠调小音量模拟。

## 第一轮数据集组合方案

第一轮数据集命名为 `CapsWriter Tech ASR Eval v1`，目标规模控制在 160 条左右；加入低增益派生后，总样本控制在 180 到 200 条。

### 原始样本组合

| 模块 | 数量 | 来源 | 目的 |
|------|------|------|------|
| 中文真实低语 | 40 | AISHELL6-Whisper | 测中文真实低语声学能力 |
| 英文真实低语 | 20 | wTIMIT 或 CHAINS，若下载/授权受阻则暂缓 | 测英文真实低语声学能力 |
| 中文技术讲解 | 35 | Chinese-LiPS 的 `KJ` 科技主题 | 测中文技术讲解和教育演示类内容 |
| 英文技术词汇 | 35 | Tech-Sentences-For-ASR-Training | 测 API、CLI、DevOps、编程词汇 |
| 英文技术演讲 | 20 | TED-LIUM 技术/科学类片段 | 测自然英文技术表达 |
| 商业技术口语 | 10 | Earnings-22 chunked | 测商业会议、口音、电话会风格 |

如果英文真实低语数据源落地成本过高，第一版允许先降级为 140 条：

| 模块 | 数量 |
|------|------|
| 中文真实低语 | 40 |
| 中文技术讲解 | 35 |
| 英文技术词汇 | 35 |
| 英文技术演讲 | 20 |
| 商业技术口语 | 10 |

### 派生样本策略

第一轮只做一类派生：`low_gain`。

| 派生条件 | 处理范围 | 目的 |
|----------|----------|------|
| `low_gain` | 从中文技术讲解、英文技术词汇、英文技术演讲/商业技术口语中抽约 40 条 | 测当前无 AGC 管线下，技术内容音量降低后的退化程度 |

第一轮暂不做 EQ、混响、噪声、复杂麦克风仿真。

原因：

- 第一轮需要先建立可解释基线，避免变量过多。
- 真实低语由真实低语数据集覆盖，不用 EQ 伪装。
- 当前最需要回答的是：现有预处理下，技术内容在低幅度输入时是否明显退化。

## 数据集来源说明

| 数据集 | 用途 | 注意事项 |
|--------|------|----------|
| AISHELL6-Whisper | 中文真实低语与正常语音对照 | CC BY-NC-SA 4.0，仅作为本地研究评测口径 |
| wTIMIT / CHAINS | 英文真实低语候选 | 下载和授权可能比 Hugging Face 数据集麻烦，第一轮可作为可选项 |
| Chinese-LiPS | 中文科技主题讲解 | 优先筛选 `KJ` 科技主题 |
| Tech-Sentences-For-ASR-Training | 英文开发者技术词汇 | 适合 API、CLI、DevOps、编程术语 |
| TED-LIUM | 英文技术/科学演讲 | 只抽少量技术类片段，避免偏离 CapsWriter 场景 |
| Earnings-22 chunked | 商业会议与电话会风格 | 少量加入，用于覆盖商业技术口语 |

### v1 下载落地状态

2026-07-06 已创建 `evals/datasets/capswriter_tech_asr_v1/download_sources.py`，下载策略是只拉 v1 所需的最小源文件或单个 shard，不下载公开数据集全量。

已落地：

- `AISHELL6-Whisper`：2026-07-08 用户 HF 访问申请已通过；已补齐 `AISHELL6-Whisper_info.csv`、`text_sentence`、`w2n.txt`、`metadata.tar.gz`、`test.tar.gz`（约 1.7GiB），用于中文真实低语样本抽取。
- `Tech-Sentences-For-ASR-Training`：小型仓库完整下载，当前本地有 205 条音频和 205 条文本。
- `Chinese-LiPS`：已下载元数据和 `processed_val.zip`，后续优先从 validation split 的 `KJ` 科技主题抽 35 条。
- `TED-LIUM`：已下载 `AudioLLMs/tedlium3_test` 单个 test parquet shard，后续抽 20 条。
- `Earnings-22 chunked`：已下载一个较小 chunked parquet shard，后续抽 10 条。

待处理：

- 中文真实低语是 v1 的关键维度，不能用 `low_gain` 替代；但数据源必须来自 Hugging Face、AI-SHELL 官方平台或作者认可入口，禁止接入来源不可审计、绕过审批或疑似泄露的数据包。
- `TED-LIUM` parquet 首次下载时曾因脚本早期 `local_dir` 逻辑产生 `data/data/` 嵌套落点；脚本已兼容复用该文件，后续抽样脚本应统一搜索实际本地文件或在 manifest 构建前做规范化路径处理。

## 第一轮首要问题

第一轮调优优先解决以下问题。

### 0. 先跑通本地子仓库后端与配置收敛

在建立正式评测结果之前，必须先完成后端接入基线：

- ✅ 2026-07-06 已确认 macOS 服务端实际导入根目录 `mlx-qwen3-asr` 子仓库源码包，实测路径为 `/Users/edgar/programs/CapsWriter-Offline/mlx-qwen3-asr/mlx_qwen3_asr/__init__.py`。
- ✅ 2026-07-06 已确认 `qwen_asr_mlx` 仍通过 CapsWriter 的 `QwenASRMLXEngine` 进入 worker 进程，但主路径已不再走旧 `WorkPipeline` 分片推理和拼接。
- ✅ 2026-07-06 runner 已落地，`qwen_asr_mlx` 主路径以 `AudioFeedPatch` 按时序 feed 同一个 `task_id` 的 package runner；当前 P0 先实现“流式喂音频 + final 离线完整结果”，尚未实现录音过程中提前处理稳定 `InferenceChunk`。
- ✅ 已完成语义收敛：`task_id` 是完整识别任务标识；服务端 worker 执行单元已经统一改名为 `Work`，不再复用 `Task` 表达完整任务语义。
- ✅ 已把当前 `return_timestamps`、`return_chunks`、`max_new_tokens`、`num_draft_tokens`、`verbose` 等推理级入口集中到 `mlx-qwen3-asr` 包内 `CapsWriterRunnerConfig`，避免 CapsWriter 外层和包内两套配置同时生效。
- 2026-07-06 接入的启动预热与`set_wired_limit`只验证了API调用，未证明长期驻留。2026-09-19修正为package对全部真实权重buffer执行`mlock`，常驻开关关闭时不调用任何锁页/Metal额度接口；开启却锁页失败时拒绝启动，具体生命周期及真机验证入口见`docs/macos-architecture-decisions.md`第九节。
- 保留 CapsWriter 外层的最小产品配置入口，避免破坏多后端工厂结构。

完成这一步之后，再开始固定 manifest 的正式基线评测。

### 1. 建立可重复评测闭环

必须先生成固定 manifest，并能在本机稳定跑完。

完成定义：

- manifest 固定记录 `sample_id`、语言、来源、场景、声学条件、音频路径、参考文本。
- 每轮评测样本顺序和抽样结果稳定。
- 输出包含识别文本、CER/WER、耗时和失败样本列表。

### 2. 分清内容错误和声学错误

不能只看总分。需要按条件拆分：

- 中文真实低语。
- 英文真实低语。
- 中文技术讲解。
- 英文技术词汇。
- 英文技术演讲。
- 商业技术口语。
- 技术内容 `low_gain`。

目标是判断当前主要瓶颈到底是技术词汇不认识，还是低语/低音量声学条件导致退化。

### 3. 验证当前管线对低音量是否敏感

当前管线没有 AGC 或响度归一化。`low_gain` 样本用于回答一个具体问题：

> 同一批技术内容，只降低音量后，识别错误是否明显增加？

如果 `low_gain` 相比 `clean` 明显退化，后续才讨论是否需要产品层输入增益或 AGC；第一轮不提前改预处理。

### 4. 评估真实低语可用性

真实低语不是调小音量。第一轮必须单独看真实低语结果。

完成定义：

- 中文真实低语至少有独立 CER 统计。
- 英文真实低语若数据源落地，则独立统计 WER；若暂缓，必须在结果中明确标注缺口。
- 不把 `low_gain` 结果当作真实低语结果。

### 5. 为后续模型和解码参数调优提供基线

第一轮不直接追求最优参数，而是形成可比较基线。

后续调优项包括：

- 1.7B-8bit 与 1.7B-4bit 的质量差异。
- `language` 强制指定与自动识别的差异。
- `context` 对技术词汇识别的帮助。
- 解码参数和 max token 策略对短句、低语、技术词的影响。

这些都必须基于同一套 v1 manifest 做对比。

## 当前不做的事

- 不要求用户自录数据。
- 不新增产品预处理。
- 不做复杂 EQ、噪声、混响增强矩阵。
- 不追求全语言评测。
- 不跑大型全量 benchmark。
- 不把 LibriSpeech/FLEURS 作为本阶段主评测集。

## 后续落地顺序

1. 把服务端依赖切到可验证的本地 `mlx-qwen3-asr` 源码加载方式。
2. 增加最小导入路径检查，确认服务端实际加载根目录子仓库源码。
3. 梳理并收敛 MLX 推理级配置，把 prompt、language、generation、预热和 wired memory 等策略放到子仓库包内集中管理。
4. 创建 `evals/datasets/capswriter_tech_asr_v1/` 数据集目录。
5. 写入数据源清单和 manifest 字段规范。
6. 编写固定抽样脚本，先落地中文低语、中文技术、英文技术三类。
7. 生成 `clean` 与 `low_gain` 样本。
8. 在 `evals/drivers/` 中实现 CapsWriter 后端评测驱动：可构造旧服务端风格的 `Work` 作为临时基线；runner 落地后主路径必须以 `task_id` 调用同一个 package runner。
9. 产出第一份基线报告，回写 `CLAUDE.md` 当前阶段状态。

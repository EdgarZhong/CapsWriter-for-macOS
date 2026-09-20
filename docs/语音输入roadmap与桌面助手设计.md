# CapsWriter 语音输入 Roadmap 与桌面语音 LLM 助手设计汇总

> 本文档汇总 2026-09-17 ~ 09-19 与 ChatGPT 的两条对话 thread 及 nmem 记忆中的设计结论。
>
> **信息来源与可靠性说明**
> - 主 thread：「开源流式语音输入方案」（114 条消息，09-17 18:35 → 09-19 13:32），导出存档 `diagnogs/nmem_context/thread_6aabc244_开源流式语音输入方案_full.txt`
> - 第二 thread：「桌面本地语音 AI 助手 LLM 选型」（22 条消息），存档 `diagnogs/nmem_context/thread_6aae0354_桌面本地语音AI助手LLM选型_full.txt`
> - nmem 记忆：`c24aa75c`（升级调研与设计定稿）、`3fab695e`（转录错误模式）
> - ⚠️ **可靠性分级**：文中 ChatGPT 搜索所得的外部事实（第三方 benchmark、模型规格、第三方仓库）**未经独立核实**。尤其「Confucius4-R2T2（网易有道 2026-09-17 发布）」一说，ChatGPT 在对话中自己承认搜索无法核实该仓库，**疑似幻觉**；R2T2 相关的 LSP、16s 滚动窗口、GGUF 等细节一律视为未验证线索，不得作为已确认事实引用。
> - ⚠️ **内存基线修正**：对话中反复引用的「Qwen 2.3GB wired 不可回收」来自 `diagnogs/mem_profile/20260919/report.md` 的 vmmap 误读，该推论已在 2026-09-19 会话中撤回（原快照根本没有 wired 列）。「idle phys_footprint ≈ 2.6GB、短句峰值 3.2–3.6GB、长音频 4.7GB」的 footprint 样本仍可作为历史容量参考。

---

## 一、桌面语音 LLM 助手：定位与首要功能点

**定位**：把 CapsWriter 从单纯的语音输入软件，升级为**本地常驻的桌面语音 LLM 小助手**。

**首要功能点（按优先级）**：

1. **问答**：语音提问 → 本地 LLM 回答
2. **翻译**
3. **改写**
4. **转录结果二次处理**：对 CapsWriter ASR 输出做润色/纠错/格式化

项目已有雏形代码可关联：`LLM/大助理.py`、`LLM/小助理.py`、`LLM/翻译.py`。

**LLM 选型结论（第二 thread）**：

| 角色 | 选型 | 说明 |
|------|------|------|
| 主文本脑 | **MiniCPM5-2B 4bit MLX + No Think** | M5 芯片 4bit 实测约 29–32 tok/s |
| 视觉脑 | **LFM2.5-VL-3B 4bit** | 屏幕/UI/OCR/截图理解；走 mlx-vlm 或 llama.cpp，避开 oMLX |
| 文本 fallback | LFM2.5-2.6B | MiniCPM 不可用时的备选 |

**资源协同**：ASR 与未来常驻的 ~2B Q4 文本 LLM 共存是硬约束——这正是推动 ASR 内存压降（见 FireRed 路线）的根本原因。

**未来交互设想**：灵动岛特效（见第六节），语音入口复用现有 Caps 长按；灵动岛与新增桌面助手能力均不在本轮实施范围。

---

## 二、产品化打包与分发（客户端主导架构已确认，待实施）

> **范围更新（2026-09-20）：本轮最高优先级为新增 Dashboard 窗口的 UI/UX 重构与 DMG 分发。UI 重构仅限 Dashboard，灵动岛暂不实施；ASR 推理调优明确不在本轮范围内，也不是产品化前置条件。** 延续已确认架构：客户端负责数据采集、人机交互状态机、服务调用编排及结果呈现；热词摘出为服务，继续使用现有 Python 和算法，无需统一插件底座或统一服务接口。当前实现仍未迁移，执行进展统一见 `../CLAUDE.md`。

**用户旅程目标**：下载 DMG → 拖入安装 `.app` → 完成必要的权限/模型引导 → 开始听写；日常通过 GUI 配置和管理。模型采用首次下载并支持本地导入，不随 DMG 打包。

**已确认方向**：

- **原生产品壳**：Swift/SwiftUI + AppKit 客户端，DMG 单 App 分发，默认菜单栏常驻，Dashboard 按需打开，GUI 支持设置、词库与推理方案管理。
- **客户端权威**：现有录音、快捷键和未来选中区/选中状态、Force Click、截图、剪贴板等采集全部归客户端；人机交互核心状态机、调用编排、结果编辑/呈现/上屏全部归客户端。未来入口只明确归属，不要求首版全做。
- **自由调用服务**：ASR、热词、未来 LLM 与其他服务分别提供能力，使用适合各自任务的语言及接口；客户端决定调用哪些服务、顺序和数据流。无需先造统一插件接口、插件运行底座或后端工作流编排器。
- **热词服务化**：下一阶段将现有 Python 热词逻辑摘为可调用服务，保留模糊匹配与别名；先保持现行算法、参数与替换行为，不做 Swift 移植或上一轮提出的算法调优。
- **自包含交付**：App 包含所需运行时和依赖，客户端提供权限引导、自启动与自带本地服务管理入口；具体托管与旧安装迁移方案在实施设计中细化。

**架构规格入口**：职责、服务边界、分发约束与待细化事项统一见 [macOS 架构决策第零节](macos-architecture-decisions.md#零产品化目标架构客户端主导编排与独立计算服务)。当前仍运行 launchd 双 agent；目标架构已确认，但尚未实施迁移。

### 热词与别名：现状审计与后续议题（保留 Swift 可行性评估）

**最终决策**：保留音素模糊匹配与别名这一产品能力，下一阶段通过服务调用继续使用现有 Python 实现。此前“迁到 Swift 原生热词引擎”的建议不作为实施方向，以下保留现状证据与可行性分析供后续参考。用户最关注的是跨词边界的非语义替换：相邻字原本不属于同一个词，却被组合为替换目标；改法以后再讨论，本轮不调优。与服务端 context-aware 的结合也属于后续议题。

**实际链路**：`PhonemeCorrector.update_hotwords()` 将目标词及每个别名都转成可检索音素序列；`get_phoneme_info()` 为连续中文调用 pypinyin，为英文生成字母序列；`rag_fast_rf.FastRAG` 用倒排索引、RapidFuzz 对齐与 OSA 距离粗筛；`fuzzy_substring_search_constrained()` 用加权编辑距离精筛；最后按分数、覆盖长度贪心消解重叠并替换。当前发布替换阈值为 0.85，相似候选阈值为 0.6。

- 已有边界约束，但中文边界是单字边界，没有保留自然语言词边界用于替换决策。pypinyin 内部存在词组匹配与读音消歧，不能据此称整个链路完全没有分词。
- 中文是声母/韵母/声调近似匹配；英文是拼写匹配，不是英文发音模型；跨语言错写主要依赖显式别名建立映射。
- 标点和空格在音素流中被跳过，原文区间替换可能跨过并吞掉标点；音似分数不是“应该替换”的置信概率；同音异义词即使分数 1.0 也可能改错。

**独立合成样本实测**（pypinyin 0.55.0、RapidFuzz 3.14.5，阈值 0.85/0.6；不读取用户词库，不代表真实误替换率）：

| 测试词库 | 原文 | 当前输出 | 得分 |
|---|---|---|---|
| 先进 | 先。进来再说 | 先进来再说 | 1.0 |
| 长江，别名为常将 | 我经常将文件保存到桌面 | 我经长江文件保存到桌面 | 1.0 |
| 事实 | 这件事是对的 | 这件事实对的 | 0.9167 |
| 权力 | 保障公民的权利 | 保障公民的权力 | 1.0 |
| 权力、权利 | 大家全力支持 | 大家权力支持；交换词库行顺序后变成大家权利支持 | 1.0 |

正常别名对照：`CapsWriter | caps writer` 可将“我在用 caps writer 输入”变为“我在用 CapsWriter 输入”；英文词内保护对照：词库 AI 不改写“he said hello”。`Claude | cloud` 会把“We use cloud storage”改为“We use Claude storage”，说明当前显式别名没有作用域或语境限制；是否符合用户意图须由别名策略定义。

**迁移难度判断（历史备选评估，非下一阶段任务）**：不存在必须保留 Python 运行时的算法依赖。词库/别名管理较低难度，倒排索引与加权编辑距离为中等难度；较难部分是拼音词组数据与多音字语义、Python/Swift 字符位置映射、正则兼容和行为回归。当前运行路径不使用 Numba，RapidFuzz 可考虑通过 C/C++ 边界复用，或实现并验证 Swift 等价算法。保留 C++ 内核也能让客户端脱离 Python。

本机 Swift 6.3.2 探针确认 NLTokenizer 可将“我经常将文件保存到桌面”分为“我/经常/将/文件/保存/到/桌面”。但系统 mandarinToLatin 将“银行”转换为 `yín xíng`、“重复”转换为 `zhòng fù`，现有 pypinyin 对照为 `yín háng`、`chóng fù`。因此不能直接用系统转写替换 pypinyin；建议固定词典版本、迁移读音规则并允许用户指定发音。当前本地 pypinyin 词典规模为 41923 个单字条目、47111 个词组条目。

**后续算法议题（待另行讨论，不属于下一阶段服务化任务）**：

1. 将词库数据、音素检索、替换决策拆开；检索返回候选、来源别名、原文范围及各项得分，决策保留“不改原文”选项。
2. 标点/换行形成硬边界，允许热词规则显式处理词内空格；分词作为边界与排序证据，结合用户词库处理技术词/OOV，不把系统分词结果作为唯一硬门槛。
3. 区分用户明确要求的固定替换、可带作用域的别名、需要谨慎自动应用的音似纠错；增加短词保护、候选分差与歧义保留策略，不能仅提高一个全局阈值。
4. 由客户端编排热词服务与 ASR/context 能力，使用同一份词库避免标准词与别名漂移；可选发音、作用域与替换策略等扩展字段以后再定。当前低阈值候选已可进入可选 LLM 后处理；ASR 录音协议则透传静态 `Config.context`，尚未自动由词库构建动态上下文。当前句转录后的候选无法影响此前已经完成的第一次识别；若用于同句 ASR 重解码，是独立的第二阶段能力。
5. 下一阶段仅以现有输入输出作服务抽取的兼容对照，保留已知误替换样本。后续单独讨论算法优化时再明确应改变哪些行为，并衡量修正召回、误改率、别名成功率、真实词库规模下延迟和内存；不把 NLP/LLM 纠错升级变成首版 DMG 的前置条件。

参考：[Apple NLTokenizer](https://developer.apple.com/documentation/naturallanguage/tokenizing-natural-language-text)、[pypinyin](https://github.com/mozillazg/python-pinyin)、[RapidFuzz C++](https://github.com/rapidfuzz/rapidfuzz-cpp)、[Swift/C++ 互操作](https://www.swift.org/documentation/cxx-interop/)。本轮没有实现完整 Swift 引擎，不能据 API 探针宣称迁移完成或性能已达标。

---

## 三、两条体验路线（用户拍板）

| 路线 | 哲学 | 行为 |
|------|------|------|
| 真离线模式 | **WKD 哲学** | 只反馈「正在听 / 正在识别」，**不展示 raw partial**，完整结果一次上屏 |
| 真 streaming 模式 | **stable-only** | stable 文本直接 append 上屏，**append-only，客户端永不退格 / rollback** |

**明确排除**：拿非流式离线模型强行做流式（伪流式累计重算 O(T²)，Qwen3-ASR 官方与 Fun-ASR-Nano 的 streaming 均属此类）。

---

## 四、三条腿 ASR Roadmap（已定稿）

```text
                       CapsWriter ASR
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
 Offline / Context    Offline / Accuracy     Streaming
          │                 │                 │
     Qwen3-ASR        FireRedASR2-AED    Native Streaming ASR
          │                 │           (X-ASR / Zipformer
 Active Context          Beam Search        Transducer 等)
 Historical Context       N-best          modified beam search
          │                 │                 │
 模型内部 Context      Context Rerank     Stable Gate
          │                 │           (LCP 横向 beam 共识
          └────────┬────────┘            + 纵向时间一致
                   │                      + 尾部 1~2 token 保护)
               Final Text                     │
                   │                     stable_delta 协议
                   └───────────┬───────────────┘
                               │
                       CapsWriter Client
                   永不 Backspace / rollback
```

三条腿分别解决三个不同问题：

- **Qwen Context 路线**：解决「模型不知道你正在聊什么」——个性化/上下文精度
- **FireRed AED 路线**：解决「裸 ASR 能不能更准、同时资源更小」
- **Native Streaming 路线**：解决「常驻最轻 + 真流式 + stable-only 体验」

**用户决定：优先 offline 路线**（streaming 精度可能明显下降、客户端热词搭配未验证、改动面太大）。

**Streaming 路线已知技术背景**（thread 前段，部分未经核实）：
- Qwen3-ASR 官方 / Fun-ASR-Nano streaming 均为伪流式（audio_accum 累计重算）
- llama.cpp 已支持 Qwen3-ASR GGUF + Metal
- Apple 侧三级路线：Level 1 GGUF 转换验证 → Level 2 复刻 rolling window streaming → Level 3 借鉴 antirez/qwen-asr 做 encoder cache
- R2T2 的 LSP（Longest Stable Prefix）/ append-only 理念可作为参考思路（来源未核实）

---

## 五、FireRed AED 离线方案（Offline v2 候选，msg 101/103 结论）

**路线选择结论**：先把 FireRedASR2-AED 做成 Offline v2 候选，Qwen3-ASR 保留为现有稳定基线和 Context-aware 对照组。

**精度对比（官方同组评测，未经独立核实）**：
- FireRedASR2-AED 普通话四测试集平均 CER **3.05%** vs Qwen3-ASR-1.7B **3.76%**
- 粤语独立 benchmark 中 Qwen 反而略优——真实表现受领域/语种影响
- **中英混说无公开同测试集直接 benchmark**：中文 FireRed 强、纯英文 Qwen 可能占优（旧一代 FireRed LibriSpeech 1.93/4.44 WER vs Qwen3 1.63/3.38）；用户的高密度技术中英混说场景必须实测

**推荐部署栈（V1）**：

```text
FireRedASR2-AED (~1.1B 专用 AED)
    ↓ sherpa-onnx 官方 INT8
encoder.int8.onnx   779 MB
decoder.int8.onnx   398 MB    （权重合计 ≈1.18GB）
    ↓
sherpa-onnx / ONNX Runtime CPU EP
    ↓
常驻 CapsWriter ASR worker（新增 FireRedAEDBackend）
```

**内存预算（工程预估，非实测，部署后须按 Qwen 同款 footprint 方法重测）**：

| 状态 | 预估 phys_footprint |
|------|--------------------:|
| 理论下限 | ~1.2GB |
| idle 常驻 | **1.4–1.7GB** |
| 2–6s 短句峰值 | **1.6–2.1GB** |
| 10–20s | 1.8–2.4GB |

目标：相比 Qwen（idle 2.6GB / 峰值 3.2–3.6GB）常驻省约 1GB、峰值省 1.3–1.8GB。**达不到则迁移价值打折。**

**CPU 而非 GPU 的理由**：
- ASR（CPU INT8）与未来文本 LLM（Metal/GPU）**异构布局**，不撞计算单元
- CPU pages 可被 macOS 压缩/换出，调度更灵活；**不建议对 FireRed 再 mlock 整个模型**（要的是免冷启动，不是永不回收）
- 注意：统一内存下 CPU/GPU 仍共享带宽与散热，「LLM 生成时继续语音输入」的并发场景是明确设计目标，不是禁止项
- 部署顺序：V1 CPU 2 线程实测 → V2 线程数调优 → V3 CoreML EP（看是否落到 ANE）→ V4 才考虑 GPU

**延迟预期（估算）**：3–5s 语音约 0.5–1.0s、10s 约 1.3–2.0s——比 Qwen（2–6s 音频 0.25–0.6s，RTF≈0.065）慢，但仍在「正常可用」范围。

**两个待验证缺口**：
1. sherpa-onnx INT8 公开示例实际是 **greedy_search**；FireRed 原模型 PyTorch 默认 `beam_size=3`，但 INT8 Mac 快速路径的 beam/N-best 能力需单独验证（N-best 是外部 contextual reranking 的前提）
2. 中英混说实测（用真实录音小样本 A/B，重点看 Agent/Codex/MLX/streaming 等技术词谁更容易听坏）

**验证判据**：同一批 PCM 跑 Qwen 与 FireRed，看四件事——真实错词风格、短句延迟、idle footprint、短句 peak footprint。精度 ≥ Qwen 且内存显著下降 → 升级为 Offline 主后端；否则放弃回 Qwen Context 路线。

---

## 六、灵动岛交互设计（保留设想，暂不实施）

> 2026-09-20 范围确认：本节仅保留未来设计参考，不属于本轮 Dashboard UI/UX 重构或 DMG 的实现与验收范围。

- **位置与体量**：刘海正下方，克制的小区域
- **Listening 态**：轮廓随声音轻微颤动 + **一根细横线**（不是音量条），以中心为基准随音量向左右伸缩
- **Streaming 模式**：stable 文本直接 append 上屏；unstable 默认不展示（可选灰色 preview，只出现在灵动岛内部）
- **松手后未转完**：同一根横线切换为**左右游移 loading**（不换 spinner），轮廓停止颤动
- **完成**：收起
- **状态机**：`Idle → Listening → Processing → Final Commit → Dismiss`

灵动岛同时承载语音输入反馈与桌面助手交互。

---

## 七、精度策略：错误形态优先于 CER

**已记录的错误模式**（记忆 `3fab695e`）：
1. 领域实体 / 英文技术词：如 `8bit → 巴比特`
2. 中文近音：如 `实验 → 时间`、`灵动岛 → 灵动到`
3. 生成式后处理自作聪明：如乱加书名号

**关键认知**：值得优化的不只是 CER，而是**「错误的形态」**——一个实体错误比一堆虚词错误更恼人。

**Context-aware 分两步**：
- **v0（先做）**：当前输入框最近一段文本 → 简单实体/关键词提取 → 去重限长 → `context=`。先验证对「闭源→币源、流式→流失、Nemotron→Numatron、Agent/Codex/MLX」这类错误的实际改善幅度，再决定是否值得继续投资
- **完整版**：`Active Context + Historical Personal Context + 用户纠错词库 → Context Builder`（两层）

**MLX 精度排查支线已结案**：GPT-6 未查出问题；用户回 Windows 实测发现两边有类似错误，判断遇到的是**模型精度上限**，非 Mac 迁移回归；吞尾与中英切换两个真 bug 已由 GPT-6 解决。现有 Qwen 方案可视为「已知可用 baseline」。

**产品问题优先级（用户真实使用排序）**：
1. 偶发转录错误恼人
2. 高内存压力下延迟崩坏
3. 热词失效（有一处实现写错待修）
4. UI / 交互产品化
5. 离线延迟（不是大问题）

---

## 八、评测基础设施（打包分发之后、三条路线之前的前置工程）

**下一步排序（msg 107 结论）**：
1. **冻结当前 Qwen baseline**（打 tag，此后一切实验回答「相比 baseline：精度/内存/延迟/错误形态怎么变」）
2. **极简评测 harness**：同一批 WAV 一键跑任意 backend，统一输出 `transcript / latency / RTF / memory_idle / memory_peak`——这是最值钱的基础设施，先于任何新路线
3. FireRed AED 最小实验（见第五节验证判据）
4. Qwen Context v0
5. Native Streaming 最后（改动面最大、变量最多）

**数据三层结构**：

| 层 | 内容 | 用途 |
|----|------|------|
| Public Set | 公开 benchmark 抽样，自带真值 | 版本门禁、模型选择 |
| Personal Golden Set | 几十条高信息密度个人样本（技术词、快口语、含糊、中英混说） | 个人场景回归 |
| Shadow Set | **9000 条历史录音（无真值，不浪费）** | differential testing：多模型输出比对，只人工看不一致的 ~7%；另可统计真实 utterance 时长分布（P25–P95）指导 Golden Set 分层 |

**用户纠偏**：自动捕获「ASR 输出 → 用户修订」是不可能的（向第三方 App 注入拿不到修订真值），除非文字经过自己的暂存输入框；菜单栏暂存框是可行的主动标注路径，但实践证明人还是懒得标——所以 Golden Set 建设**不依赖个人标注**。

**Core Golden Set v1 方案（msg 113，180 分钟，约 1500–2200 条，按 ~7.6× 实时的批量速度单轮全量约半小时）**：

| 场景 | 时长 | 权重 | 数据来源 | 测什么 |
|------|-----:|-----:|----------|--------|
| 自然中文口语/对话 | 45 min | 25% | MagicData-RAMC + WenetSpeech | 停顿、重复、口语表达 |
| 中英混说 | 45 min | 25% | ASRU 2019 Code-Switch（Dev_CS 为主） | 英文词/产品名/技术词/中式发音 |
| 低声/耳语 | 36 min | 20% | AISHELL6-Whisper | 不能大声说话的场景 |
| 干净普通话基本盘 | 24 min | 13% | AISHELL-1 + Common Voice zh-CN | 防基础中文能力退化 |
| 复杂环境/口音/设备 | 20 min | 11% | WenetSpeech + Common Voice zh-CN | 笔记本麦、轻噪声、口音 |
| 纯英文 sanity check | 10 min | 6% | LibriSpeech / Common Voice | 完整英文不至于崩 |

要点：
- **耳语/小声是一级产品场景**（用户拍板）：AISHELL6-Whisper 有平行录制（同文本正常+耳语各约 30h），可算 **Whisper Penalty = CER_whisper − CER_normal**；注意其许可证 CC BY-NC-SA 4.0，商用前需重核
- **中英混说 + 低声合计 45%**，刻意高于传统 benchmark 权重——这是 CapsWriter 用户画像（开发者/AI 用户/知识工作者，桌面口述、夹英文术语、办公室压低音量）的真实分布
- **指标不止 CER**：CER（中文）/ WER（英文）/ MER（混说）+ English-token recall/precision + Whisper Penalty + RTF + idle/peak RAM + entity accuracy；分项结果永远不被总分取代
- manifest 预留 `entities` / `context` 字段，Context 路线起来后同批音频直接复用
- WenetSpeech TEST_NET 有官方 label error 修正，抽样须基于修正版标注
- utterance 时长分层：`<4s 20% / 4–10s 40% / 10–30s 30% / >30s 10%`，分布向 9000 条真实录音对齐

与既有 `evals/` 的关系：2026-07 已下载部分源数据（AISHELL6-Whisper 等，见 CLAUDE.md「ASR 评测脚手架」），本节 180 分钟方案是 2026-09 的最新设计口径，落地时以本节为准。

---

## 九、资源与内存约束

- 机器：16GB MacBook Air（无风扇，热预算敏感）
- 现状（Qwen3-ASR 1.7B-8bit MLX）：idle phys_footprint ≈ 2.6GB，短句峰值 3.2–3.6GB，长音频 4.7GB（2026-09-19 report.md 历史样本；wired 归因已撤回，见文首说明）
- 未来叠加 ~2B Q4 文本 LLM（权重 ~1.1–1.4GB + runtime/KV cache ~0.5–1.5GB）后，FireRed 路线下合计预算：正常 ~3–4GB、重负载 ~4–5GB
- 典型工作流 ASR 峰值与 LLM 生成峰值天然错开；并发场景（LLM 生成中继续语音输入）是设计目标而非禁止项

---

## 十、未决问题清单

- [x] **产品化架构方向确认**：客户端主导全部采集、交互状态、调用编排与结果呈现；计算服务按需调用；热词保留 Python 服务化；不建统一插件底座（仅文档确认，未实现）
- [ ] 下一阶段实施细节：客户端迁移、热词服务接口/词库更新、旧 launchd 安装迁移、自包含 DMG 构建与签名、模型交付方式（见架构决策第零节；动态进度见 CLAUDE.md）
- [ ] 热词跨词边界非语义替换的优化方案（后续另议，不纳入本轮架构升级）
- [ ] FireRed AED INT8 实机 phys_footprint / 延迟 / 真实错词风格（产品化之后的后端优先实验）
- [ ] 中英混说实测对比（Qwen vs FireRed，技术词密度场景）
- [ ] sherpa-onnx INT8 路径的 beam/N-best 能力验证（外部 contextual reranking 的前提）
- [ ] Streaming 路线的客户端热词与 stable 协议搭配验证
- [ ] R2T2 / Confucius4 相关信息独立核实（当前视为未验证线索）
- [ ] Golden Set v1 构建（180 分钟方案，复用 `evals/` 已下载源数据）
- [ ] 桌面助手功能的产品化形态（入口、与灵动岛状态机的整合）尚未细化设计

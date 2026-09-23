# CapsWriter for macOS 产品化架构定稿

> 定稿日期：2026-09-23（用户最新澄清）。
> **效力声明**：本文是当前架构最高口径。凡既有文档（含 `macos-architecture-decisions.md` 第零节、`语音输入roadmap与桌面助手设计.md`）与本文冲突之处——尤其是"热词摘出为独立服务""保留 Python headless client 作为客户端终局"——一律以本文为准。

这轮产品化集成不是把现有 Python client 套进 Swift 壳，而是重新划清 **macOS 原生客户端、独立推理服务、Python 算法组件、离线研究工具** 四者的职责边界。

核心目标：**CapsWriter.app 成为真正的 macOS 客户端主体；凡直接参与用户正常输入体验、需要与 macOS 系统交互的逻辑尽量原生化；只有确实需要独立生命周期和资源管理的推理能力才作为独立 Python 服务存在。**

---

## 一、总架构原则

### 1. 不为"统一"制造抽象

不设计统一的 `CapsWriter Engine`、统一 Server 或统一 RPC 层。

一项能力是否独立成服务，只看它本身是否具备：

- 独立生命周期；
- 独立的大模型/推理资源；
- 需要长期预加载；
- 需要单独崩溃隔离；
- 需要流式通信；
- 将来可能独立启动、停止或替换。

满足才适合 `独立 Python process + 独立 WebSocket + 独立端口`。例如 ASR 走 WS 6016；未来本地 LLM 是另一个 process、另一条 WS、另一个端口。两个推理服务本来就是两个服务，不在其上人为套统一 Engine。

### 2. 模块独立 ≠ 进程独立

热词是典型反例：热词算法保持独立模块，但它是 `ASR raw result → hotword correction → 客户端后续处理` 的一环，属客户端结果处理链，**不服务化**：

- 不占独立端口、不是 ServiceManager 管理的独立服务；
- 不做单独 health check、不需要独立生命周期；
- 短期保留 Python 实现时，作为 CapsWriter.app 托管的 **private helper**（App 启动它、随 App 退出）；
- 通信用轻量本地 IPC（stdin/stdout JSON），不用 TCP/WebSocket；
- 将来迁 Swift 收益足够高时直接消掉 helper；本轮不为"纯 Swift"重写成熟算法。

---

## 二、最终目标拓扑

不再保留"Python headless client"。收敛为：

```text
CapsWriter.app  [Swift，唯一客户端主体]
│
├── ShortcutEngine
├── AudioEngine
├── ASR Session / WS Client
├── FocusManager
├── PasteInjector
├── ClipboardPolicy
├── Editor
├── Dashboard / MenuBar
├── Notification / Permission
├── Annotation / 用户输入链数据采集
├── Result Pipeline
│
├── private Hotword helper [Python，可暂留]
│   └── 不监听端口，不是独立服务
│
└──── WS 6016 ────► ASR Python Process
                    └── MLX / Qwen ASR

未来：
CapsWriter.app
└──── 另一条 WS ──► Local LLM Python Process
```

**Swift App 自己就是 client。**

---

## 三、必须原生化的能力

判断标准：凡是用户正常使用 CapsWriter 时，直接参与输入流程、UI 状态或 macOS 系统交互的能力，归 Swift App。本轮至少包括：

### 1. 快捷键与录音控制状态机

- 最终由 Swift `ShortcutEngine` 统一管理；设置页只提供经过适配验证的**预设快捷键**，不开放用户自由录制。
- 初步产品语义（设置页可多选启用）：
  - 长按 Caps Lock → press-to-talk
  - 长按 Fn → press-to-talk
  - Ctrl + Fn → toggle recording（第一次开始，第二次结束）
- 底层抽象为行为 `pressToTalk` / `toggleRecording`，录音系统不感知 Caps / Fn 等具体键。
- 现有 Caps Lock → F18 → hidutil 路线已稳定，**本轮不为纯 Swift 推翻成熟实现**；但 ownership 归 `ShortcutEngine`。
- 原则：稳定的底层机制可暂时复用；新的产品状态机不再堆在 Python client 上。

### 2. Audio

macOS 录音归 Swift，优先 Apple 原生 `AVAudioEngine` / `AVAudioInputNode`。理由：麦克风权限、音频设备切换是系统能力；后续流式 ASR 需要稳定音频流；App 生命周期、前后台状态与错误提示由 Swift 控制更自然。

### 3. WebSocket Client

6016 的 ASR WebSocket 客户端迁 Swift。不再保留一个 Python client process 只为"录音 → WS → 收结果"。

### 4. 焦点、剪贴板、上屏

全部 Swift 原生化，不再放 Python。

### 5. 编辑框

现有 PyObjC 编辑框迁 Swift / AppKit。打开编辑框、修改文本、Enter 确认、Esc 放弃、恢复焦点、上屏、上一条状态，都属产品客户端行为，不再由 Python 遥控 AppKit。

---

## 四、文本上屏技术路线：只保留 Paste（已定稿）

**只有一个 Text Injection Backend：Paste**，即 `NSPasteboard + CGEvent Cmd+V`。

不再设计 AX direct input、CGEvent Unicode typing、keyboard.write、AppleScript fallback、多级注入 fallback 链。

当前结果模式与未来 Streaming 都建立在同一条 Paste 路线上：Stable Gate 产生稳定 chunk 时逐段 Paste（stable chunk 1 → Paste → stable chunk 2 → Paste …）。不为 Streaming 提前引入第二套注入技术。

原则：一个行为只走一条明确路径，只尝试一次，不构建复杂注入 fallback 链。

---

## 五、两个完全正交的上屏用户设置

两者彼此独立，且都与底层 Paste 技术实现无关。

### 1. 焦点策略

设置建议名：`返回录音开始位置上屏`。

- **关闭**：转录结果出来后，直接向当前焦点 Paste 一次。
- **开启**：录音开始瞬间记录 FocusAnchor（pid、bundle ID、window/application context、`AXFocusedUIElement`）；结果完成后：验证原目标仍存在 → 激活原应用 → 恢复原焦点元素 → Paste。
- 注意：现有代码只记录 `frontmostApplication`，只是"回到原 App"，不是真正的"回到原输入框"。迁 Swift 后升级为真正的 AX focus anchor。
- **原焦点失效**（原窗口关闭、输入框消失、AX element invalid、App 退出、无法可靠恢复）：**禁止本次自动上屏，不 fallback 到当前焦点**，并主动发系统通知（如"上屏失败：录音开始时的输入位置已不可用"），不依赖 macOS 自己的错误按键声。目的是避免误粘到用户正在操作的另一个窗口。

### 2. 剪贴板保留策略

不能描述成"是否使用剪贴板"——Paste 路线下无论如何都必须临时使用剪贴板。真正含义：**Paste 完成后，转录结果是否继续占据系统剪贴板**。

设置建议名：`保留转录结果到剪贴板`。

- **开启**：原 clipboard = A → 写 transcript = B → Cmd+V → 最终 clipboard = B。
- **关闭**：保存 A → 临时写 B → Cmd+V → 安全恢复 A。
- 恢复必须利用 `NSPasteboard.changeCount` 或等价所有权判断：写入 B 时记录对应 changeCount → Paste → 恢复前再次检查；clipboard 仍是本 App 写入的那一版才恢复 A；期间用户/其他 App 已复制 C 则不恢复。绝不能发生"用户刚复制 C，CapsWriter 又把 A 强行写回"。
- 准确语义：在安全且仍拥有本次临时 clipboard 修改的情况下恢复原内容。

### 正交组合示例

| 组合 | 原焦点失效时行为 |
|---|---|
| 返回原焦点 + 保留结果 | 不 Paste；转录结果保留在 clipboard；通知"上屏失败：原输入位置已不可用，转录结果已保留到剪贴板。" |
| 返回原焦点 + 不保留结果 | 不 Paste；不改变用户原 clipboard；通知"上屏失败：录音开始时的输入位置已不可用。" |

不能为了焦点失败而偷偷改变剪贴板设置。

---

## 六、热词的最终边界

现有热词算法（pypinyin、音素拆分、模糊匹配、FastRAG、规则纠错）现在不重写 Swift，也不提升为独立服务。

```text
热词 = 客户端后处理组件

Swift ResultPipeline
    ↓
Private Python Hotword Helper
    ↓
corrected result
```

不要 `Swift → WS 6019 → Hotword Service`；也不要为"减少 Python 进程"把它硬塞进 ASR server。热词属客户端逻辑链，只是算法实现暂时还是 Python。是否 Python，不决定是否服务化。

---

## 七、研究数据采集与离线工具的边界

原则：**发生在用户正常输入过程中的数据采集与交互状态，归 Swift；脱离用户正常使用流程的离线数据工程，继续 Python。**

### Swift 负责（产品客户端的一部分）

用户正常输入时自然产生的：录音 session、raw ASR text、hotword corrected text、编辑框 final text、Enter/Esc、上一条、标记当前案例、音频与文本关联、annotation event、正常输入流程中的 manifest 原始记录。

尤其编辑框、上一条语义、用户修正文本、标注触发，不能因为最终用于研究数据集就继续留在 Python client。

### Python 保留（普通用户运行时不启动）

manifest 清洗、dataset build、去重、筛选、数据切分、批量转码、batch inference、CER/WER、A/B evaluation、benchmark、正式评测、统计、报告生成、实验脚本。

---

## 八、结果数据必须保留不同阶段

不只保留最终 hotword corrected 文本，至少保留：

```text
raw_text         = ASR 模型原始输出
corrected_text   = 热词后处理之后
final_text       = 用户编辑框最终确认后的文本
hotword_matches  = 热词命中记录
```

用途：产品上屏用 final/corrected；数据集仍能知道模型原始错误；可评估热词修了什么、用户最终修正了什么。不让热词层洗掉 ASR 原始错误信息。

---

## 九、判断准则（施工中遇到归属问题时使用）

### 是否应该 Swift 原生化？

属于以下者优先 Swift：macOS 系统交互、用户实时输入体验、UI、App 状态、快捷键、权限、焦点、剪贴板、音频设备、session 生命周期、用户实时数据采集。

属于以下者可以 Python：算法、模型推理、离线数据处理、benchmark、研究工具。

### 是否应该独立服务？

只有确实需要独立生命周期、独立模型资源、长时间常驻、独立崩溃隔离、独立流式通信、可单独启动/停止/替换，才用 `独立 process + WS + port`。

不要因"它是 Python"就自动服务化；也不要因"想统一架构"就把多个实际无关的服务揉成一个总 Server。

---

## 十、允许暂时保留的 Python（生产运行时）

1. **独立推理服务**：ASR Python process；未来 LLM Python process。各自独立、各自端口、各自生命周期。
2. **客户端内部算法 helper**：Hotword Python helper——不是 service、不开放端口，只是暂时复用成熟算法。
3. **离线研究工具**：`evals/`、`tools/`、dataset scripts、benchmark——与普通用户运行时解耦。

---

## 十一、当前架构定稿

```text
CapsWriter.app [Swift]
│
├── Native Client Runtime
│   ├── ShortcutEngine
│   ├── AudioEngine
│   ├── ASR WebSocket Client
│   ├── Session / Result Pipeline
│   ├── FocusManager
│   ├── PasteInjector
│   ├── ClipboardPolicy
│   ├── Editor
│   ├── Annotation Capture
│   └── Dashboard / MenuBar
│
├── Private Hotword Helper [Python，暂留]
│   └── local IPC only（stdin/stdout JSON）
│
└──── WS 6016 ───► ASR Python Process
```

未来增加本地 LLM：`CapsWriter.app ── WS 6016 ── ASR Process`，`── another WS ── LLM Process`，不额外制造统一 Engine。

本轮重点不是追求"代码语言纯度"，而是把**职责、生命周期和系统边界划正确**：成熟算法可以继续 Python；系统交互和产品 runtime 必须逐步收归 Swift；真正的推理服务才独立进程化。

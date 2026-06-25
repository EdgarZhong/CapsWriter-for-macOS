# macOS 权限模型调查现状（辅助功能 + 输入监控）

> ⛔️ **状态：已结案归档（2026-06-22）· 仅作过程留痕，勿再据其行动。**
> 第二轮核查已完成，权限引导已据此**重写落地**。**最终设计以
> [`docs/macos-architecture-decisions.md`](macos-architecture-decisions.md) 第六节
> 『权限引导（2026-06-22 重订）』为准**。本文下面的部分早期推断已被最终收敛取代，
> 阅读时以架构决策第六节为唯一权威，本文只用于追溯"当时怎么想的"。
>
> **被最终收敛取代的早期结论（勿再采信本文这几处）**：
> - ❌ "健康判据改为全程回调心跳" → 实际采 **option C 单次启动校验 ping**；运行期仍用 `CGEventTapIsEnabled`。
> - ❌ "探测层迁移到文档化 CG API" → `IOHIDCheckAccess` 经核查有官方文档，**不迁**，仅降级为提示。
> - ❌ "request API 注册 IM 条目" → 实测无效；改 **「AX 就绪后补一次 tap 尝试」** 注册。
> - ⏸ "稳定 DR 自签名证书根治 stale" → 是**另一条独立根因线**，本轮未做。
>
> 下文原始置信标记：✅已确认 / 🟡有依据待核查 / ❓真空白 / ❌已被本轮推翻。

---

## 0. 一句话现状

我们原来把"权限引导收敛为仅辅助功能"，但 2026-06-22 实测证明 **CGEventTap 实际同时受输入监控门控**，必须把输入监控重新纳入。围绕"如何干净地处理输入监控"，本轮厘清了：用哪些 API、能不能感知三种故障态、以及真正的根因件是什么。

---

## 1. 事实地基（带置信标记）

| # | 结论 | 置信 |
|---|------|------|
| F1 | 键盘 CGEventTap 在 macOS 10.15+ **同时需要辅助功能 + 输入监控** | ✅ |
| F2 | 缺权限时 `CGEventTapCreate` 返回 `NULL` | ✅ |
| F3 | **签名(cdhash)变化后**：权限在旧签名下授予、重签后 `CGEventTapCreate` 返回**非 NULL 但回调永不触发的「死 tap」**（"a non-nil tap is not a healthy tap"） | 🟡 |
| F4 | 因此 **`CGEventTapIsEnabled` 不是可靠健康判据**；真正权威判据是**回调心跳是否到达** | 🟡 |
| F5 | 输入监控的**文档化 API** 是 `CGPreflightListenEventAccess()`(bool) / `CGRequestListenEventAccess()`；我们现用的 `IOHIDCheckAccess/IOHIDRequestAccess` 是**未公开 IOKit 符号**（三态，但非官方） | 🟡 |
| F6 | 辅助功能探测 `AXIsProcessTrusted()` 是**二值**，分不出「无条目/关/stale」 | ✅ |
| F7 | TCC 改动对运行中进程不生效，**必须退出重启** | ✅ |
| F8 | **跨 build 存活授权的正解 = 稳定的 Designated Requirement (DR) 签名**（build N+1 与 N 同 DR），Apple DTS(Quinn) 建议；验证 `codesign -d -r - <app>` | 🟡 |
| F9 | TCC 按 **responsible process** 归属授权；launchd agent / 非 GUI 进程的责任进程可能不是二进制本身 | 🟡 |
| F10 | stale 态下 `CGPreflightListenEventAccess/IOHIDCheckAccess` 的**精确返回值** | ❓真空白（无文档） |
| F11 | rdar://7381305：**先调 `AXIsProcessTrustedWithOptions` 再调 `IOHIDRequestAccess` 会坑掉后者**（输入监控条目/弹框不出现）——疑为"初始引导面板里条目不显示"的元凶 | 🟡（仅搜索摘要，未读原文） |

---

## 2. 四象限工具箱（更新为正牌 API）

方法论：把"与权限表交互"的每个原语，按 {辅助功能 / 输入监控} × {探测 / 操作} 拆成单一职责接口。

### 象限① 辅助功能 × 探测
- `probe_ax()` = `AXIsProcessTrusted()` —— **二值**，仅"现在生不生效"。

### 象限② 辅助功能 × 操作
- `prompt_ax()` = `AXIsProcessTrustedWithOptions({prompt:True})` —— Unknown 时写条目+弹框。⚠️ 见 F11 顺序坑。
- `open_pane_ax()` —— 纯导航。
- 删条目 —— ❌ 无 API，只能弹窗让用户手动「−」。

### 象限③ 输入监控 × 探测
- `probe_im()` = `CGPreflightListenEventAccess()` —— **文档化，但只 bool**。
- `probe_im_3state()` = `IOHIDCheckAccess(ListenEvent)` —— 三态(0/1/2)，非官方，可选增强。
- ⚠️ stale 态下两者都可能撒谎（说 granted 但 tap 死）。

### 象限④ 输入监控 × 操作
- `request_im()` = `CGRequestListenEventAccess()` —— 文档化请求+弹框。
- `register_im_via_tap()` = `CGEventTapCreate` 尝试 —— 触发写条目；⚠️ stale 态返回非空但死。
- `open_pane_im()` —— 纯导航。
- 删条目 —— ❌ 无 API，只能弹窗让用户手动「−」。

### ★ 横跨两者的权威判据原语（新增，最重要）
- `tap_is_healthy()` = **回调心跳：tap 回调最近是否真的在到达**（**不是** `CGEventTapIsEnabled`）。
  stale 态下 tap enabled 却收不到事件，只有它能识破。

---

## 3. 三种问题情景 × 工具箱体检

| 情景 | 能否**感知** | 能否**处置** | 工具箱够不够 |
|------|------|------|------|
| **① 新机器从零（无条目）** | ✅ `probe_ax`=False + `probe_im`=False | ✅ `prompt_ax`→拿 AX→`register_im_via_tap` 写条目→`open_pane_im`+引导拨开关→重启后 `tap_is_healthy` 确认 | **够**，但要避开 F11 的 AX→IM 顺序坑 |
| **② stale（条目在/开关开/失效）** | ⚠️ 探测会撒谎；唯一可靠=`tap_is_healthy`=False 但探测乐观 → 矛盾即 stale | ❌ 不能自愈：无删条目 API → 弹窗让用户手动「−」+重启；**根治靠稳定 DR（F8，工具箱之外）** | **不够**：缺 `tap_is_healthy` 就感知不到；只能引导手动删 |
| **③ 运行途中撤权（3a 删条目 / 3b 关开关）** | ✅ 3a 探测翻+心跳掉；3b `probe_im`=Denied+心跳掉 | ✅ 复用①对应片段重新引导；**关键：原地恢复不退出，杜绝死循环** | **够**，前提是有 `tap_is_healthy` |

**体检三句话：**
1. 现有工具箱的致命缺口只有一个：把健康判据从 `CGEventTapIsEnabled` 升级成 `tap_is_healthy`（回调心跳）。补上它 ①③ 干净可处理、② 才感知得到。
2. ②（stale）工具箱本质只能"感知 + 引导用户手动删"，不能自愈，真正归宿是**稳定 DR 签名**把它从源头消除。
3. 探测层换文档化 CG API；IOKit 三态版作可选增强；**谁都不能当健康判据**，健康只认回调心跳。

---

## 4. 被本轮调查推翻 / 需修订的旧决策

| 旧决策（CLAUDE.md / 第六节） | 本轮结论 |
|------|------|
| ❌「权限引导收敛为仅辅助功能，输入监控仅作人工提示」 | 推翻：CGEventTap 受输入监控硬门控，必须重新纳入（已记于 CLAUDE.md P0） |
| ❌ 用 `CGEventTapIsEnabled` 作运行态健康判据（M7.2） | 修订：stale 态会骗过它，须改回调心跳 `tap_is_healthy` |
| 🟡 fatal→退出→KeepAlive 重启 | 修订：易成死循环，③ 改原地恢复不退出 |
| 🟡 长期 ad-hoc 签名 | 修订：应上稳定 DR 自签名证书（F8），从根上消除 stale |

---

## 5. 落地顺序建议（根因件优先）

1. **根因件 A：稳定 DR 自签名证书** —— 生成稳定证书 + 改 `build_launcher.sh` 用它签（不再 ad-hoc）。直接消除情景②。
2. **根因件 B：回调心跳 `tap_is_healthy`** —— 取代 `CGEventTapIsEnabled` 作权威健康判据。
3. 探测层迁移到文档化 CG API（`CGPreflightListenEventAccess`/`CGRequestListenEventAccess`）。
4. 在 A/B 就位后，再按四象限原语**干净编排 ①②③ 三条独立流程**，并避开 F11 顺序坑、杜绝死循环。

> 诊断脚本：`tools/perm_probe.py`（四象限原语探针，含责任进程上下文打印；从终端跑量的是终端身份，量 CapsWriter 需在 .app 身份下运行）。

---

## 6. 待第二轮独立核查（交给另一个 AI 的指令，见附录）

重点核查 F3/F5/F8/F9/F10/F11 与三情景的 API 可处理性。附录为完整核查指令。

---

## 来源
- Apple Developer Forums — Question about IOHIDRequestAccess: https://developer.apple.com/forums/thread/696673
- CGEvent Taps and Code Signing: The Silent Disable Race: https://danielraffel.me/til/2026/02/19/cgevent-taps-and-code-signing-the-silent-disable-race/
- rdar://7381305 (openradar): https://openradar.appspot.com/7381305
- HackTricks — macOS Input Monitoring / Accessibility: https://hacktricks.wiki/en/macos-hardening/macos-security-and-privilege-escalation/macos-security-protections/macos-input-monitoring-screen-capture-accessibility.html

---

## 附录：第二轮核查指令（可整段交给另一个 AI）

```
# 任务：独立核查 macOS 键盘 CGEventTap 的权限模型（辅助功能 + 输入监控）

## 背景
我在做一个 macOS app，用 CGEventTap 拦截键盘事件（Caps Lock 长按录音）。
它同时需要「辅助功能 Accessibility」和「输入监控 Input Monitoring / kTCCServiceListenEvent」两个 TCC 权限。
dev 期用 ad-hoc 签名，每次 build 的 cdhash 都会变。
请独立核查下面每条结论是「证实 / 推翻 / 无定论」，并给出权威来源
（优先级：Apple 官方文档 > Apple Developer Forums 中 DTS 工程师 Quinn 的回帖 > 系统头文件 > 高质量技术博客/开源实现）。

## 一、API 正确性（四象限）
[辅助功能·探测] C1: AXIsProcessTrusted() 只返回 bool，无法区分「未授权/开关关/签名失效」三态。
[辅助功能·操作] C2: AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt:true}) 仅在「无历史决定」时弹原生框，并把 app 写进辅助功能列表。
[输入监控·探测] C3: 文档化 API 是 CGPreflightListenEventAccess()（返回 bool）。
[输入监控·操作] C4: 文档化请求 API 是 CGRequestListenEventAccess()。
[输入监控·探测] C5: IOKit 的 IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) 是未公开符号，返回三态 granted(0)/denied(1)/unknown(2)，与 CG 版功能等价。
建议搜索：
  "CGPreflightListenEventAccess CGRequestListenEventAccess documentation"
  "IOHIDCheckAccess kIOHIDRequestTypeListenEvent IOHIDAccessType granted denied unknown"
  "AXIsProcessTrustedWithOptions kAXTrustedCheckOptionPrompt behavior add to list"

## 二、核心行为
C6: macOS 10.15+ 键盘 CGEventTap 必须同时有「辅助功能」+「输入监控」。
C7: 缺权限时 CGEventTapCreate 返回 NULL。
C8（最关键·签名失效）: 当权限是在旧签名(cdhash)下授予、之后重签导致 cdhash 变化时，
    CGEventTapCreate 会返回「非 NULL 但回调永不触发」的死 tap（"a non-nil tap is not a healthy tap"）。
C9（未解）: 在上述 stale 状态下，CGPreflightListenEventAccess() / IOHIDCheckAccess() 到底返回什么？
    （我没找到权威答案，请重点查这条。）
C10（登记时机·有冲突）: app 是因为「尝试创建 CGEventTap / 调 CGRequestListenEventAccess」才出现在
    系统设置「输入监控」列表里，还是只有用户在设置里手动授予后才出现？两种说法我都见过，请厘清。
C11（顺序坑·待证实）: rdar://7381305 称「先调 AXIsProcessTrustedWithOptions 再调 IOHIDRequestAccess
    会导致后者失效（输入监控弹框/条目不出现）」。请确认是否存在这个调用顺序依赖。
建议搜索：
  "CGEventTapCreate returns NULL Input Monitoring Accessibility 10.15 Catalina"
  "CGEventTap non-nil tap not healthy code signing callbacks not firing re-sign"
  "CGEventTap silent disable code signing cdhash TCC"
  "IOHIDRequestAccess not working AXIsProcessTrustedWithOptions order rdar 7381305"
  "app appear in Input Monitoring list when CGEventTap created vs user grant"

## 三、跨重建存活 & 责任进程
C12: 让 TCC 授权跨 build 存活的正解 = 用「稳定的 Designated Requirement (DR)」签名，
     使 build N+1 与 build N 的 DR 相同（Apple DTS Quinn 建议）。验证：codesign -d -r - <app>。
C13: TCC 改动对运行中进程不生效，必须退出重启。
C14: 对 launchd agent / 非 GUI 进程，TCC 按「responsible process」归属授权，可能不是二进制本身。
建议搜索：
  "TCC stable designated requirement build N+1 same DR Quinn Apple Developer Forums"
  "tccutil reset ListenEvent Accessibility bundle id"
  "TCC responsible process launchd agent code requirement"

## 四、三种问题情景：现有 API 能否处理（请逐条评估）
情景①「新机器从零，两个列表都没有条目」：
  - 能否可靠地让 app 出现在输入监控列表？正确的调用顺序是什么（注意 C11 的坑）？
情景②「条目在、开关开，但因重签 cdhash 变化而失效(stale)」：
  - 能否用 API 程序化地检测到 stale？（探测 API 会不会撒谎？）
  - 有没有任何 API 能程序化移除/刷新失效条目，还是只能弹窗让用户手动「−」删除？
情景③「运行途中被撤权」：3a 条目被删 / 3b 开关被关
  - 系统如何通知？辅助功能撤销是否发 kCGEventTapDisabledByTimeout？
    输入监控撤销是否「静默停发事件」？分别如何在运行时检测？
建议搜索：
  "macOS first run Input Monitoring entry not appearing TCC"
  "programmatically remove reset stale TCC entry Input Monitoring"
  "kCGEventTapDisabledByTimeout DisabledByUserInput revoke accessibility detect"
  "Input Monitoring revoked tap still enabled events stop silently"

## 输出要求
对 C1–C14 + 情景①②③，逐条给出：【证实/推翻/无定论】+ 一句话依据 + 来源链接。
特别标出与我结论相矛盾的地方。
```

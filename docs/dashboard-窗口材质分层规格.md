# Dashboard 窗口材质分层规格

> 状态：需求已确认，待实现。2026-09-20 由用户口述多轮收敛，主会话整理。
> 适用对象：`native/CapsWriter` 的 Dashboard 主窗口（SwiftUI + AppKit，macOS 26 为主，13–25 需回退不残）。
> 本文档是唯一口径；实现者有歧义时回到本文档，不要自行发挥。

## 一、要的是什么

用户要一个**整体磨砂半透明的 Dashboard 窗口**：透过窗口能看到下方桌面/其他窗口的颜色与光线（是柔和的磨砂透光，不是清晰透视，也不是实色不透明）。

窗口由**恰好三层**材质构成，从下到上：

| 层 | 名称 | 职责与观感 | 层级 |
|---|---|---|---|
| 1 | 背板 | 铺满**整个窗口**（含标题栏区域下方、侧栏区域下方）。轻微磨砂 + 少量半透明：透得出下方的颜色和明暗变化，但看不清下方的文字细节 | 最低 |
| 2 | 标题栏层 | 窗口顶部一条，承载红绿灯、窗口标题「CapsWriter」、右侧外观三段切换。带点模糊；内容向上滚动时，内容在这层**下面渐隐消失**；标题文字始终清晰可读 | 第二高 |
| 3 | 侧栏玻璃 | 左侧导航栏的磨砂玻璃面板，磨砂程度**比背板更轻、更透**；它盖在背板之上，叠加后视觉上应更通透，而不是更糊 | 最高 |

### 第 2 层的两条硬性要求

1. **厚度**：标题栏只有一层厚度——红绿灯、窗口标题、外观切换控件就在这一层里。不允许出现「标题栏 + 另外一条模糊带」两条上下堆叠的带子。现在的实现就错在这里（顶部一条实色条 + 下面再一条渐变层，太厚）。
2. **滚动渐隐**：设置、词库、转录历史这类长页面向上滚动时，内容必须渐隐进标题栏层下缘（Safari/系统设置的效果）。渐隐是柔和的模糊渐变，不是生硬的裁剪线。
3. **降级方案**：如果标题栏区域技术上做不成模糊，就用 Safari 式做法——标题栏正常实色，仅在其下缘做一小条模糊渐变接住滚动内容。这也是可接受的，不要为模糊硬造第二条厚带。

### 第 3 层（侧栏）的要求

- macOS 26 上用系统原生侧栏玻璃即可，不要再叠加自定义磨砂层。
- 侧栏玻璃的磨砂要比背板更轻微、更透明。

## 二、明确禁止（都是实测踩过的坑）

1. **禁止用 `window.isOpaque = false` + `backgroundColor = .clear` 来调透明度**：会导致浅色模式下标题栏/侧栏交界处出现一块诡异的白色残块（带圆角和缺口的白块），已复现两次。
2. **禁止全透明**：透明度高到能看清下方窗口文字，内容可读性崩塌，用户明确不接受。
3. **禁止假透明**：材质视图装不上（异步拿 window 时机不对）导致实际全不透明，也已发生过——窗口级配置必须在 `viewDidMoveToWindow` 里做。
4. **禁止两条带**：标题栏层和渐隐层是同一层，不是上下两条。

## 三、技术线索（已查证）

- **滚动渐隐首选系统能力**：macOS 26 / iOS 26 起 SwiftUI 提供 `scrollEdgeEffectStyle(_:for:)`（`.soft` / `.hard` / `.automatic`），系统工具栏/标题栏会自动对滚到下方的内容做模糊渐隐。参考 [WWDC25 Session 323「Build a SwiftUI app with the new design」](https://developer.apple.com/videos/play/wwdc2025/323/)（"an automatic scroll edge effect keeps controls legible… a subtle blur and fade effect applied to content under system toolbars"）与 [Apple 文档 scrollEdgeEffectStyle](https://developer.apple.com/documentation/swiftui/view/scrolledgeeffectstyle(_:for:))。配合 `fullSizeContentView` + `titlebarAppearsTransparent` 让滚动内容延伸到标题栏下方即可，**不要自绘渐变层**。
- **背板做法**：`NSVisualEffectView`，`blendingMode = .behindWindow`，插到 contentView 最底层铺满；窗口本身保持默认不透明。材质选型即透明度选型（大致从实到透：`.windowBackground` → `.underWindowBackground` → `.contentBackground` → `.popover` → `.menu`），用「双色背景差值测试」（见验收 1）选定档位，预期落在 `.underWindowBackground`～`.popover` 之间。
- **侧栏**：macOS 26 的 `NavigationSplitView` 侧栏自带玻璃；确认不要再用 `.background` 给它叠加材质（macOS 13–25 回退可用 `SidebarMaterial`，允许比 26 略实）。
- **减少透明度**（`accessibilityReduceTransparency`）：所有层退回不透明实色，可读性优先。这一行为已有代码口径，保留。

## 四、验收标准（逐条可执行，全部通过才算完成）

1. **透光可感知**：把窗口分别压在「纯白窗口」和「纯黑桌面」上各截一张图，取正文同一区域（避开卡片）的均值 RGB，两图每通道差值应 > 6（证明确实透光）；同时在正常桌面下看不清下方窗口的文字（证明磨砂足够）。
2. **无残块**：浅色、深色各截全窗口图，标题栏、侧栏交界、四角无任何异形色块/白块。
3. **标题栏单层**：目视确认红绿灯、窗口标题、外观切换控件在同一水平条带内，其下方没有第二条独立色带；条带总高度与系统设置/Safari 顶栏相当。
4. **滚动渐隐**：在转录历史页（200 条数据）向上滚动，内容在标题栏层下缘柔和渐隐；滚动全程标题文字清晰。
5. **侧栏更透**：同一背景下目视对比，侧栏比正文背板更透；两层交界处无突兀断层。
6. **深浅色**：两种外观各自满足 1–5；外观切换后无需重启即生效。
7. **减少透明度**：系统设置开启「减少透明度」后，整窗退回不透明实色且布局不变形。
8. **回归**：`swift run --package-path native/CapsWriter DashboardCoreChecks` 全部通过；`bash tools/build_dashboard.sh` 成功。

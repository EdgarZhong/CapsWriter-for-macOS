# CapsWriter-Offline 当前迭代看板

> 更新：2026-09-20 · 工作分支：`mac-dev`
> 本轮聚焦客户端产品化：Dashboard UI/UX 重构与 DMG 分发并列最高优先级。历史已完成事项和过去月份的流水记录已从当前看板移除。

## 三项任务与优先级

| 优先级 | README 用户向事项 | 本轮范围与完成定义 | 当前状态 |
|---|---|---|---|
| P0 | 简洁精美的 GUI：客户端 UI/UX 重构 | **仅限新增 Dashboard 窗口**；承载设置、词库与现有推理方案管理入口，采用原生 macOS 侧栏与「概览、设置、词库、推理方案」四页；完成界面、真实能力接入与用户验收 | 开发中：四页导航、真实状态概览与原生深浅色外观已接入；听写设置/词库/模型操作待实现 |
| P0 | .dmg 一键安装包 | 自包含 App 与所需运行时/依赖；完成安装、权限/模型引导、服务生命周期、旧安装迁移与升级保留用户数据的验证；用户无需克隆源码或安装 Python/uv/Homebrew | 本轮启动，待实施设计与开发 |
| 后续 | ASR 推理精度调优 | **不在本轮范围内**，不作为 Dashboard 或 DMG 交付的前置条件；保留现有可用推理基线 | 暂缓，后续迭代另行推进 |

## 已确认的实施边界

- 设置只对应 `config_client.py` / `config_server.py` 的配置管理；客户端授权使用独立权限窗口。配置是否迁移 JSON 待澄清。
- 外观仅保留右上角自动/浅色/深色三个图标的原生分段控件，带提示与辅助功能标签。


- 新增转录历史：查找、便捷复制、编辑；编辑结果按现有编辑框标注语义进入 eval 库。先完成概览与模型页，再接入历史，不另建标注标准。
- 模型下载默认使用 Hugging Face 国内镜像 `https://hf-mirror.com`；明确配置的 `HF_ENDPOINT` 保留为覆盖入口。


- 概览按真实服务、权限与资源状态显示文案和图标，并提供对应处理入口；仅全部就绪才显示“准备好听你说话”。
- 推理方案以列表展示，顶部刷新重新检测资源；缺模型或运行时时提供下载，打开资源目录独立常驻。两个方案已确认为 Qwen3-ASR 1.7B-8bit 与 1.7B-4bit。
- 视觉继续收敛：撤掉手工键帽与装饰声纹，改用大号简洁 Caps Lock 图标和紧凑状态入口；外观只保留右上角自动/浅色/深色三段控件。

- 窗口材质分层规格已重写为纯视觉口径（只描述「长什么样」，不含技术选型）：[Dashboard 窗口材质分层规格](docs/dashboard-窗口材质分层规格.md)。该规格为唯一视觉口径；当前实现与规格的差距见「本轮执行状态」。


- 视觉方向：原生 macOS 26 Liquid Glass，磨砂半透明侧栏；复用 `assets/icon/app-icon` 素材；支持跟随系统、浅色、深色并持久化外观选择。

- UI 重构只做新增 Dashboard 窗口；**灵动岛设想暂不实施**，不借本轮重做菜单栏、录音浮层或结果编辑框的 UI/UX。必要的 Dashboard 打开入口和安装引导服务于上述两项交付。
- 延续上一轮架构决策：Swift/SwiftUI + AppKit 原生客户端负责数据采集、人机交互状态机、服务调用编排、结果呈现与上屏；ASR、热词等服务负责各自计算，不建立统一插件底座或后端编排器。
- 架构迁移是 Dashboard 与 DMG 的支撑工作，不另扩展产品任务。热词抽取继续复用现有 Python 实现，保留模糊匹配、别名、算法、阈值和替换行为；不移植 Swift 算法，不顺带优化跨词误替换。
- ASR 精度/性能/内存调优、context-aware、新模型与 streaming 路线、评测基础设施扩建以及新增桌面助手能力均留待后续；本轮只验证迁移后现有能力的兼容性。
- 当前仍为 Python 客户端与 launchd 双 agent 的运行实现；目标架构已确认，不代表 Swift 客户端或自包含 DMG 已完成。

## 实施前待细化

- Dashboard 已确认采用原生 macOS 侧栏与「概览、设置、词库、推理方案」四页；细化设置项、词库操作和异常反馈。
- 原生客户端与现有 ASR/热词服务的调用接口、热词更新方式及行为兼容验证。
- 自带本地服务的托管、重连、退出、崩溃恢复、自启动与旧 launchd 安装迁移。
- 模型交付已确认采用首次下载＋本地导入，不随 DMG 打包；继续细化构建、签名公证与用户数据目录方案。

规格入口：[产品化架构边界](docs/macos-architecture-decisions.md#零产品化目标架构客户端主导编排与独立计算服务)、[产品化与后续 Roadmap](docs/语音输入roadmap与桌面助手设计.md#二产品化打包与分发客户端主导架构已确认待实施)。

## 本轮执行状态

- 文档整理完成：用户 `readme.md` 与 main 发布版本逐字对齐；补齐 `README.dev.md` 开发说明；历史流水已备份至 `.archive/` 并移出看板。
- 当前实现：`native/CapsWriter` 提供五页原生导航（概览/设置/词库/推理方案/转录历史）、真实状态概览、现有 App Icon、持久化外观切换；设置页经 `tools/dashboard_settings.py` 读写 `config_client_local.py`/`config_server_local.py`（白名单+备份），词库页经 `tools/dashboard_vocabulary.py` 管理 hot.txt/hot-rule.txt/hot-server.txt，转录历史经 `tools/dashboard_history.py` 检索最近 200 条并把修订追加进 eval v2；`tools/build_dashboard.sh` 生成独立开发 App。
- **材质分层交接（2026-09-20，工作区有未提交改动，仅涉及 `Appearance.swift` 与 `CapsWriterDashboard.swift`，当前可编译）**：
  - 架构基线（已确认有效，勿动）：透明 NSWindow（`titlebarAppearsTransparent` + `fullSizeContentView` + `isOpaque=false` + `clear` 背景，在 `WindowConfiguratorHostView.viewDidMoveToWindow` 设置）+ 根 ZStack 最底层 `DashboardGlassBackground`（`.behindWindow` 磨砂背板）+ 清空 window/toolbar 容器背景。侧栏顶部已去图标、软件名大号粗体。
  - 背板材质 `.popover → .sidebar`：用户确认「背板换成了正确效果」。
  - 侧栏「挖孔」尝试（`DashboardGlassBackdropView` 用 `maskImage` 开孔 + 侧栏铺 `.popover`）：**失败，侧栏反而更不透明**；且侧栏列顶部多出约一个标题栏高度的实心亮带。建议直接回退挖孔相关改动（`DashboardGlassBackdropView`、holeWidth、`SidebarWidthKey`、侧栏 background），回到「侧栏 `Color.clear` 或原生玻璃」再重新诊断。
  - 顶栏渐进模糊层（规格第 2 层）：**从未成功，当前完全缺失**。已失败两条路径：① SwiftUI 根 ZStack 挂 `NSVisualEffectView(.titlebar, .withinWindow)` + CALayer mask（完全无效果）；② AppKit contentView 顶层 overlay + `maskImage`（只见实心带、无模糊）。两条路径的代码均已删除。
  - 待解决（按优先级）：A. 去除侧栏列顶部实心亮带（来源未查明，候选：NavigationSplitView 侧栏列在标题栏区域的系统材质叠加）；B. 实现规格第 2 层顶栏渐进模糊；C. 恢复侧栏比背板更透的关系。验收口径见规格文档第五节。
  - 硬约束：禁止在用户桌面创建全屏/大面积背板窗口做采样测试（详见 AGENTS.md）；验收只许窗口截图或 alpha 分析。

- 后续：接入模型首次下载/本地导入的完整引导，推进原生客户端迁移与完整 DMG。当前开发包不含 Python/ASR，不能作为完整产品交付。
- 验证：5 组 Swift 状态兼容检查、窗口编译与开发包签名校验通过；实机检查四页导航、真实状态显示与浅色/深色切换。减少透明度和 macOS 13–25 回退仅完成代码接入，未实机验收。其余页面操作、原生听写迁移、完整 DMG 和用户验收未完成。
- 资源修正：`models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit/` 此前混入 4bit 仓库的 `config.json` 与 `README.md`（权重文件本身即官方 8bit），已按官方 8bit 仓库替换并补齐 `model.safetensors.index.json`；旧文件备份于 `.archive/qwen3-asr-8bit-metadata-20260920-110246/`，Dashboard 资源不一致警告已消除。
- 未改个人配置、词库或日常服务；开发窗口使用独立 Bundle ID。未提交、未推送。

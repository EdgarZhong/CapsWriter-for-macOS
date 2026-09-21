# CapsWriter-Offline 当前迭代看板

> 更新：2026-09-21 · 工作分支：`mac-dev`
> 本轮聚焦客户端产品化：Dashboard UI/UX 重构与 DMG 分发并列最高优先级。历史已完成事项和过去月份的流水记录已从当前看板移除。

## 三项任务与优先级

| 优先级 | README 用户向事项 | 本轮范围与完成定义 | 当前状态 |
|---|---|---|---|
| P0 | 简洁精美的 GUI：客户端 UI/UX 重构 | **仅限新增 Dashboard 窗口**；承载设置、词库与现有推理方案管理入口，采用原生 macOS 侧栏与「概览、设置、词库、推理方案」四页；完成界面、真实能力接入与用户验收 | 开发中：五页导航、设置/词库/历史桥接与真实概览已接入；材质外观及页面操作待验收，模型完整引导待完成 |
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
- **材质分层修复（2026-09-20，顶栏方案已接入，几何修正后完成两次滚动截图复测）**：
  - 已保留透明 NSWindow、根 ZStack 的完整 `.behindWindow` 背板与透明 window/toolbar 容器基线。背板组件改名 `DashboardWindowBackdrop`，业务配置为 `DashboardSurfaceStyle.backdropMaterial`；用户确认保留底层 `.sidebar` 的既有观感，改名不等于换材质。
  - 已撤回失败的挖孔、`holeWidth`、`SidebarWidthKey` 和侧栏额外 `.popover`。侧栏恢复原生背景，不以第二份材质掩盖问题；**侧栏比主画布更透尚未确认，不能标为完成**。
  - 侧栏显式隐藏自己的工具栏背景，窗口使用 `.none` 标题栏分隔。侧栏比主画布更透仍未纳入本轮。
  - 顶栏现采用 `ProgressiveTitlebarBackdrop`：代表视图只负责生命周期，真正的 `NSView.backgroundFilters` 视图安装到 `window.contentView?.superview ?? window.contentView`，并相对 `contentView` 位于其上方。
  - 首版按“52pt 主体 + 24pt 额外过渡 + 48pt 尾部”实现，用户截图确认顶栏过厚、遮住导航且主画布出现轻微全覆盖模糊；该版本已修正并备份在 `.archive/dashboard-titlebar-final-20260920/`。
  - 当前几何改为总高 52pt，底部 24pt 在这 52pt 内从无模糊过渡到最大模糊；mask 暂用滤镜 view 本地坐标，并限制 layer bounds。两次不同滚动位置的普通窗口截图观察到：顶部进入的记录被柔化，下面正文恢复清晰，未再观察到主画布持续全覆盖轻糊或额外实色横带。
  - 顶栏半径当前已回退到 64；256 版本没有通过用户验收，不能作为当前基线。22:42 归档 `.archive/dashboard-build-20260920-224413-88977/` 是用户确认的正确基线，后续外观判断以该归档和 64 源码状态为准。
  - 工具链取证与恢复（2026-09-21）：22:42 正确归档的 Mach-O 为 `minos 13.0 / sdk 26.5 / ld 1267.0`；系统更新后的 23:54 归档为 `minos 13.0 / sdk 13.0 / ld 27037.1`。已将官方 Apple CLT 26.6（产品 `140-17812`）安装到本机，当前 receipt 为 `26.6.0.0.1781586589`，Swift/SwiftPM 为 6.3.3，默认 `MacOSX.sdk` 指向 26.5，ld 为 1267。
  - 2026-09-20 23:33 的 CLT 27 / Swift 6.4 / SDK 27 事故仍保留在取证记录中；它曾触发 `SwiftUIMacros.StateMacro` 缺失，并生成 SDK 13.0 的非等价产物。旧混合 `.build` 已完整移动到 `native/CapsWriter/.build-before-clt26-restore`，未删除历史归档。
  - 因此“64 改 256 导致所有样式变化”尚未成立：半径变化与 CLT/macOS 环境变化发生了时间上的混杂；后续视觉判断继续以 22:42 正确归档和当前 64 源码状态为准。
  - 工具链恢复后的干净 `bash tools/build_dashboard.sh` 构建通过，新的 Mach-O 为 `minos 13.0 / sdk 26.5 / ld 1267.0`，开发包严格签名校验通过。普通窗口浅色/深色及侧栏收起/展开检查未见缺失控件或崩溃；切换过程中系统日志记录了非崩溃的 `AppKit Invalid view geometry: width/height is negative`，本轮不修，留给后续 UI 专项。
  - 标题控制采用 macOS 15+ 官方 `toolbar(removing: .title)`；侧栏按钮通过同一份 `sidebarVisibility` 在 `.all` 与 `.detailOnly` 间切换，移除了 NavigationSplitView 默认 sidebar toggle。macOS 26 使用同一根 `NavigationSplitView` toolbar 中的 `ToolbarSpacer(.flexible)` 将原生外观 Picker 推到 trailing，旧系统保持原自动布局。
  - 2026-09-20 普通窗口验收：侧栏展开时标题不存在、收起时 AX 树出现 `CapsWriter`、再次展开后标题消失；外观开关保留原生 segmented 样式并移动到标题栏右侧，未进入正文 overlay 或渐进模糊层。
  - 2026-09-21 外观跟随系统修复：仅主 Dashboard 右上角三档 Picker 负责外观；浅色/深色写入当前 App 的 `NSApp.appearance`，跟随系统写入 `nil` 交还 macOS 自动切换。权限窗口已移除 `dashboard.appearance` 读取和颜色方案绑定。当前系统为亮色，已实测“跟随系统 → 深色 → 跟随系统”恢复亮色。
  - 侧栏品牌区保持单行 `CapsWriter` + `for macOS`；按用户参考图实测，默认 Dashboard 窗口调整为 `1000×700pt`（普通窗口截图约 `998×702pt`），侧栏 ideal 宽度调整为 `212pt`，副标题调整为 `16pt`，品牌标题继续使用系统 `.title`。红绿灯附近的系统蓝绿色环境光晕尚未改色，未添加独立色块。
  - 上述扩散原因仍是实现层假设，截图只证明本机当前构建的两个场景；减少透明度、窗口 resize、macOS 13–25 回退和完整第五节验收仍未完成。
  - 已失败历史供排查：`NSVisualEffectView(.titlebar, .withinWindow)` 配 CALayer mask 曾无模糊；AppKit 材质 overlay 配 `maskImage` 曾只见实心带。本轮上述三次修复复测亦未通过，按 UAT 流程暂停试错并建议用原生最小复现隔离系统行为。
  - 用户已授权 computer use 操作普通尺寸开发窗口验收；最新优先级为顶栏模糊，侧栏更透暂缓。浅色和深色均已做窗口截图复测，减少透明度与其他视觉项尚未完成本轮复测。

- 后续：接入模型首次下载/本地导入的完整引导，推进原生客户端迁移与完整 DMG。当前开发包不含 Python/ASR，不能作为完整产品交付。
- 验证：5 组 Swift 状态兼容检查、窗口编译与开发包签名校验通过；实机检查四页导航、真实状态显示、浅色/深色切换及跟随系统回切。减少透明度和 macOS 13–25 回退仅完成代码接入，未实机验收。其余页面操作、原生听写迁移、完整 DMG 和用户验收未完成。
- 资源修正：`models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit/` 此前混入 4bit 仓库的 `config.json` 与 `README.md`（权重文件本身即官方 8bit），已按官方 8bit 仓库替换并补齐 `model.safetensors.index.json`；旧文件备份于 `.archive/qwen3-asr-8bit-metadata-20260920-110246/`，Dashboard 资源不一致警告已消除。
- 未改个人配置、词库或日常服务；开发窗口使用独立 Bundle ID。工具链恢复基线已提交为 `aae1247`；本轮跟随系统修复尚未另行提交，等待用户验收。

## 本轮材质修复执行计划（2026-09-20）

- 命名口径（用户已确认）：业务名称须与背板、顶栏、侧栏职责对应；背板保留已确认的系统 `.sidebar` 效果，仅将系统枚举封装为 `DashboardSurfaceStyle.backdropMaterial`，不改材质效果。
- 目标：按视觉规格消除侧栏亮带，恢复顶栏滚动模糊与侧栏通透关系；保留透明窗口、整窗 `.sidebar` 背板及透明容器基线。
- 修改范围：`Appearance.swift` 负责材质与窗口设置，`CapsWriterDashboard.swift` 负责分栏与滚动边缘接入；必要时修正 `README.dev.md` 的过时入口描述，不改变纯视觉规格。
- [x] 撤回挖孔、侧栏额外材质及宽度上报，恢复完整背板。
- [x] 接入侧栏工具栏背景配置并撤回失败的自定义滤镜；浅色未见原先独立亮带。
- [x] 顶栏纯模糊：已按最终方案接入窗口级 `CIMaskedVariableBlur`；首版几何失败后已收回为标题栏同厚度，并完成两次滚动截图复测，仍需后续覆盖更多验收场景。
- [x] 标题与工具栏联动：使用官方标题移除 API 和单一侧栏可见性绑定，完成收起/展开/再展开三态窗口验收；macOS 26 用 `ToolbarSpacer(.flexible)` 完成外观开关右对齐验收。
- [x] 核对外观切换与跟随系统回切；侧栏通透关系、减少透明度回退仍待验收。
- [x] `bash tools/build_dashboard.sh` 构建及签名校验通过；`swift run --package-path native/CapsWriter DashboardCoreChecks` 的 5 组状态兼容与 9 项就绪条件检查通过。
- [ ] 按视觉规格第五节完整验收亮带、顶栏单层/模糊、滚动柔化、侧栏更透、深浅色、减少透明度与 resize；当前仅完成深浅色和两次滚动截图的局部验证。
- 桌面限制：已授权 computer use 操作开发 Dashboard 普通窗口验收；禁止采样背板、全屏或大面积覆盖窗口。
- 最新优先级：先实现并验证顶栏模糊，侧栏更透留待随后处理。

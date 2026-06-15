# Bug Report：通知横幅图标显示为破图 / 空白

> 状态：**未解决，已 park，留待下个会话**（2026-06-15 排查）
> 本会话聚焦键盘/授权问题，此项不动渲染代码，仅记录排查结论与下一步实验设计。

## 一、现象

- UNUserNotificationCenter 弹出的**通知横幅左侧图标**显示为"破图 / 空白占位符"（本会话截图 #3、#5）。
- **既不是**卷轴（脚本编辑器，osascript 回退的特征），**也不是** CapsWriter 的液态玻璃图标。
- 通知**标题文字 "CapsWriter" 正常**，正文正常。

## 二、已确认正确（排除项，别再回头查这些）

| 项 | 结论 | 证据 |
|----|------|------|
| icns 完整性 | ✅ 10 张标准尺寸(16/32/64/128/256/512/1024 含 @2x)，全部合法 PNG | `iconutil -c iconset` 正常解包出 10 文件、尺寸全对 |
| bundle 图标绑定 | ✅ | Info.plist `CFBundleIconFile=app-icon`；`Resources/app-icon.icns` 存在 |
| LaunchServices 注册 | ✅ | `lsregister -dump`：`CFBundleIconFile=app-icon`、`icons: Contents/Resources/app-icon.icns`、`relative-icon-path` |
| Finder Get Info 图标 | ✅ 正确 | 截图 #2 |
| 设置→通知→CapsWriter 行图标 | ✅ 正确 | 截图 #4 |
| 走的是 UN 而非 osascript | ✅ UN | `~/.capswriter/logs/notify.log`: `UN 投递` |
| 通知权限弹窗图标 | ✅ 正确 | 用户首次授权时所见 |

## 三、决定性对比

**设置面板图标 ✓** vs **横幅图标 ✗**——同一个 bundle、同一个 icns。
→ 说明"**静态 bundle 图标解析链**"（Finder / 设置面板 / 权限弹窗都走它）完全正常；
   异常出在"**横幅运行时渲染链**"，这是另一条独立路径。

## 四、已试无效（别重复）

1. **刷新缓存**：`killall usernoted` + `killall NotificationCenter` + `lsregister -f CapsWriter.app` → 横幅仍破图。
2. **`setApplicationIconImage_`**：在进程内显式给 NSApplication 设 app 图标 → 横幅无变化。
   → 证明**横幅左侧图标不取自运行进程的 `applicationIconImage`**。

## 五、当前最可能假设

横幅渲染链对「**ad-hoc 签名 + 跑在非 `/Applications` 的仓库目录 + macOS 26（Darwin 25）全新系统**」这类 agent app **不予渲染 app 图标** → 破图占位。代码 / 资源 / bundle 身份全部正确，卡在**系统的信任 / 位置策略**层面。

## 六、为何"先成功后失败"难以归因（混杂变量）

1. 绑图标时**重签过一次**（cdhash 变）。
2. 期间存在**多个孤儿实例**，且启动方式不同（launchd `kickstart` 起的 vs 注册成 `application.*` 的）——**不同实例的横幅渲染可能不一样**。
3. macOS 26 全新系统，行为未知。
4. usernoted 缓存态不确定。

> 最可能：用户记忆中的"成功"其实是**权限弹窗 + Finder Get Info**（都读静态 bundle 图标，永远对），**横幅从切到 UN 起就一直是破的**；但**无法排除**某个 LaunchServices 完整注册的孤儿实例发的横幅恰好正常。

## 七、下个会话的排查建议（受控实验）

**前提**：先清场到**单实例干净状态**（无孤儿，参考本会话清场命令：`kill -TERM <pid>` + 确认 `launchctl list | grep capswriter` 无 `application.*`）。

1. **启动方式对照**：同一 `.app`，分别用 (a) `capswriter start`（launchd）与 (b) `open CapsWriter.app`（LaunchServices）各启一次、各发一条通知，比较横幅图标。
   → 区分"**启动方式**"变量。若 `open` 起的正常 → 病根是 launchd 启动方式，有便宜修法（不必动签名/位置）。
2. **签名+位置实验**：把 `.app` 复制到 `/Applications`，用**稳定自签名证书**（非 ad-hoc）签一次，从那启动看横幅。
   → 区分"**签名 + 位置**"变量。注意会产生新的 TCC 授权记录。
3. 若以上都仍破 → 基本坐实是 **macOS 26 对 ad-hoc/dev 应用横幅图标的限制**，归入"**正式 release（稳定签名 + /Applications）自动解决**"，dev 态接受。

## 八、相关代码 / 数据位置

- 通知实现：`core/client/error_bus.py`（`_init_native_notifier` / `_deliver` / `_deliver_native`）
- 诊断日志：`~/.capswriter/logs/notify.log`（记录每条通知实际走 UN 还是 osascript）
- icns 源：`assets/icon/app-icon.icns`；bundle 内 `CapsWriter.app/Contents/Resources/app-icon.icns`
- 参考截图：本会话 #3 / #5（破图横幅）、#4（设置面板正确）、#2（Finder 正确）

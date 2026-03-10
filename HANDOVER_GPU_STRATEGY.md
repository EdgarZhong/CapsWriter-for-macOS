# GPU 分工与设备选择策略交接文档

## 当前背景
用户尝试在双显卡（集显 + 独显）环境下配置 CapsWriter-Offline，目标是实现灵活的计算分工，例如：
1. **纯独显方案**：延迟最低，性能最强（当前已验证成功）。
2. **混合分工方案**：Encoder（ONNX）跑在集显，Decoder（GGUF）跑在 CPU 或独显，以降低独显负载。

## 遇到的问题
**设备 ID 动态漂移**：
- Windows 系统对 GPU 的枚举顺序（Device ID 0/1）不是固定的。
- 它受到多种因素影响：
    - 是否开启 Vulkan（高性能模式）。
    - 是否外接显示器（HDMI 直连独显会强制独显成为主设备）。
    - 系统节能策略。
- 导致 `dml_device_id = 0` 有时指向集显，有时指向独显，无法稳定锚定物理硬件。

## 已完成的工作
1. **代码层面增强**：
    - 修改了 `util/qwen_asr_gguf/inference/encoder.py`，现在支持显式传递 `dml_device_id` 并强制构造 DirectML Provider。
    - 修复了 `util/qwen_asr_gguf/inference/llama.py`，兼容了新版 `llama.cpp` 的 API（如 `llama_kv_cache_clear`），解决了崩溃问题。
    - 修复了 `asr.py` 中的 KV Cache 清理调用。
2. **配置层面验证**：
    - 成功验证了 **纯独显方案** (`dml_device_id=0`, `vulkan_device_id=0`)，此时系统判定独显为主设备。
    - 验证了在特定场景下（无外接显示器、低功耗）`dml_device_id=0` 可能指向集显。

## 下一步建议 (Next Steps)
为了彻底解决“无法稳定选定集显”的问题，建议在下一个会话中实施 **基于名称的设备选择策略**，而不是依赖不稳定的 ID。

### 1. 实现基于名称的设备查找
修改 `encoder.py` 的初始化逻辑，不再只接受 `int` 类型的 ID，而是支持字符串匹配。
- 遍历 `ort.get_available_providers()` 或使用 `dxgi` 库枚举设备。
- 允许用户配置 `dml_device_name = "Intel"` 或 `dml_device_name = "RTX 4060"`。
- 程序自动根据名称找到对应的 ID。

### 2. 完善 Vulkan 设备选择
`llama.cpp` 的 Vulkan 后端通常也支持通过环境变量指定设备，但同样存在 ID 漂移。
- 研究 `llama.cpp` 是否支持 UUID 或名称匹配。
- 或者在 Python 层通过 `vulkan` 库先枚举出物理设备的 UUID，再映射到 `llama.cpp` 的索引。

### 3. 固化配置
在 `config_server.py` 中引入新的配置项：
```python
# 显卡选择策略
gpu_selection_mode = "name" # or "index"
dml_gpu_keyword = "Intel"   # 匹配集显
vulkan_gpu_keyword = "NVIDIA" # 匹配独显
```

---
**文档创建时间**: 2026-03-10
**最后修改人**: Trae AI

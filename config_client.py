import os
import platform
from collections.abc import Iterable
from pathlib import Path

# 版本信息
__version__ = '2.5'

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# 客户端配置
class ClientConfig:
    addr = '127.0.0.1'          # Server 地址
    port = '6016'               # Server 端口

    # 快捷键配置列表
    shortcuts = [
        {
            'key': 'caps_lock',     # 监听大写锁定键
            'type': 'keyboard',     # 是键盘快捷键
            'suppress': True,      # 不阻塞按键（但录音结束会补发）
            'hold_mode': True,      # 长按模式
            'enabled': True         # 启用此快捷键
        },
        {
            'key': 'x2',
            'type': 'mouse',
            'suppress': True,
            'hold_mode': True,
            'enabled': False
        },
    ]

    threshold    = 0.3          # 快捷键触发阈值（秒）

    # macOS Caps Lock 新路线配置：
    # 1. `remap_f18` 表示运行期把物理 Caps Lock 临时映射成 F18，再由客户端监听 F18。
    # 2. `native_tap` 仅保留为历史文档口径，不再接入主程序。
    # 3. `off` 表示不接管 macOS Caps Lock。
    macos_caps_mode = 'remap_f18'
    macos_caps_remap_enabled = True
    macos_caps_remap_target = 'f18'
    macos_caps_hold_threshold_ms = 200
    macos_caps_synth_caps_hold_ms = 120
    macos_caps_restore_on_exit = True
    macos_caps_open_stream_on_demand = platform.system() == 'Darwin'

    # macOS 录音设备选择：
    # 'default'（发布默认，普适）：跟随系统当前默认输入设备，每次开流前刷新设备表；
    # 'builtin'：优先使用 Mac 内建麦克风（个人场景，例如常戴耳机、避免默认输入被
    # 系统自动切到耳机麦）。找不到内建麦时仍回退跟随默认输入。
    macos_mic_device = 'default'

    paste        = False        # 是否以写入剪切板然后模拟 Ctrl-V 粘贴的方式输出结果
    restore_clip = True         # 模拟粘贴后是否恢复剪贴板
    paste_apps   = ['WeiXin.exe', 'Telegram.exe']  # 匹配时强制粘贴

    # ===== 编辑框标注模式（macOS，2026-08-23）=====
    # True：识别结果先进原生编辑框，Enter 确认后先存标注再上屏；
    # Esc=放弃：不入标注库，非空转录仍写剪贴板但不自动上屏。False：维持旧行为直接上屏。
    # 菜单栏「编辑框模式」项运行时切换并持久化到 ~/.capswriter/state/editor-mode.json
    editor_mode = True
    # 「标记上一条」的全局热键（pynput 格式；仅 macOS 注册）。
    # 2026-08-24：⌥M 会与系统特殊字符输入冲突（打出 μ 等希腊字母），改 ⌃⌥M
    mark_problem_hotkey = '<ctrl>+<alt>+m'

    save_audio = True           # 是否保存录音文件
    audio_name_len = 20         # 将录音识别结果的前多少个字存储到录音文件名中，建议不要超过200
    
    context = ''                # 提示词上下文，用于辅助 Fun-ASR-Nano 模型识别（例如输入人名、地名、专业术语等）
    language = 'auto'           # 识别语言：'auto', 'chinese', 'english', 'japanese' 等（各引擎支持范围不同）

    trash_punc = '，。,.'       # 识别结果要消除的末尾标点

    traditional_convert = False     # 是否将识别结果转换为繁体中文
    traditional_locale = 'zh-hant'  # 繁体地区：'zh-hant'（标准繁体）, 'zh-tw'（台湾繁体）, 'zh-hk'（香港繁体）

    hot = True                 # 是否启用热词替换（统一 RAG 匹配）
    hot_thresh = 0.85           # RAG 替换热词阈值（高阈值，用于实际替换）
    hot_similar = 0.6           # RAG 相似热词阈值（低阈值，用于 LLM 上下文）
    hot_rule = True             # 是否启用自定义规则替换（基于正则表达式）

    llm_enabled = False         # macOS 初版暂不启用 LLM 润色；需要时改回 True
    llm_stop_key = 'esc'        # 中断 LLM 输出的快捷键

    enable_tray = True          # 客户端默认启用托盘图标功能

    # 日志配置
    log_level = 'DEBUG'          # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'

    mic_seg_duration = 60       # 麦克风听写时分段长度：60秒
    mic_seg_overlap = 4         # 麦克风听写时分段重叠：4秒

    file_seg_duration = 60      # 转录文件时分段长度
    file_seg_overlap = 4        # 转录文件时分段重叠

    file_save_srt = True        # 转录文件时是否保存 srt 字幕
    file_save_txt = True        # 转录文件时是否保存 txt 文本（按标点切分后的）
    file_save_json = True       # 转录文件时是否保存 json 结果（含原始时间戳）
    file_save_merge = False      # 转录文件时是否保存 merge.txt（未切分的段落长文本）

    udp_broadcast = True                # 是否启用 UDP 广播输出结果
    udp_broadcast_targets = [           # UDP 广播目标地址列表，格式: (地址, 端口)
        ('127.255.255.255', 6017),      # 本地回环广播
        # ('192.168.1.255', 6017),      # 局域网广播（示例，按需启用）
    ]

    udp_control = False             # 是否启用 UDP 控制录音（外部程序发送 START/STOP 命令）
    udp_control_addr = '127.0.0.1'  # UDP 控制监听地址（'0.0.0.0' 允许外部访问）
    udp_control_port = 6018         # UDP 控制监听端口


# 快捷键配置说明
r"""
快捷键配置字段说明：
  key        - 按键名称（见下方可用按键列表）
  type       - 输入类型：'keyboard'（键盘）或 'mouse'（鼠标）
  suppress   - 是否阻塞按键（True=阻塞，False=不阻塞）
  hold_mode  - 长按模式（True=按下录音松开停止，False=单击开始再次单击停止）
  enabled    - 是否启用此快捷键

阻塞模式说明：
  - 阻塞模式  ：长按录音识别，短按（<0.3秒）则自动补发按键，不影响单击功能
  - 非阻塞模式：对于 CapsLock/NumLock/ScrollLock 这类切换键，松开时会自动补发，以恢复按键状态

可用按键名称：

  字母数字：a - z, 0 - 9（大键盘）

  符号键：, . / \ ` ' - = [ ] ; '


  功能键：f1 - f24

  控制键:
      ctrl_l,   ctrl_r,
      shift,  shift_r,
      alt_l,    alt_gr,
      cmd,    cmd_r

  特殊键：
      space, enter, tab, backspace, delete, insert, home, end
      page_up, page_down, esc, caps_lock, num_lock, scroll_lock
      print_screen, pause, menu

  方向键：up, down, left, right

  鼠标键：x1, x2

示例配置：
  {'key': 'caps_lock', 'type': 'keyboard', 'suppress': False, 'hold_mode': True, 'enabled': True}, 
  {'key': 'f12', 'type': 'keyboard', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
  {'key': 'x2', 'type': 'mouse', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
"""


# 本地个人配置覆盖（可选，不入库，见 .gitignore 的 config_client_local.py）：
# 用于保存与发布默认值不同的本机偏好，例如 `ClientConfig.macos_mic_device = 'builtin'`。
# 在本模块导入末尾执行，保证任何使用方拿到的都是覆盖后的最终值。
_local_config_path = Path(BASE_DIR) / 'config_client_local.py'
if _local_config_path.exists():
    exec(  # noqa: S102 - 有意执行本机用户自己的配置文件
        compile(_local_config_path.read_text(encoding='utf-8'), str(_local_config_path), 'exec'),
        {'ClientConfig': ClientConfig},
    )

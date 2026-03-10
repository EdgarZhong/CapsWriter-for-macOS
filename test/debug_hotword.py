import sys
import os
import re

# 添加项目根目录到 sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config_client import ClientConfig as Config
from util.hotword.manager import HotwordManager

# 模拟 TextOutput.strip_punc，避免引入 audio 依赖
def strip_punc(text: str) -> str:
    if not text or not Config.trash_punc:
        return text
    clean_text = re.sub(f"(?<=.)[{Config.trash_punc}]$", "", text)
    return clean_text

def test_hotword_replacement():
    print("=== 热词替换调试脚本 ===")
    print(f"Config.hot: {Config.hot}")
    print(f"Config.hot_thresh: {Config.hot_thresh}")
    print(f"Config.hot_similar: {Config.hot_similar}")
    print(f"Config.llm_enabled: {Config.llm_enabled}")
    
    # 模拟输入文本 (基于用户反馈的 '读显')
    input_text = "我想要开启读显模式"
    print(f"\n输入文本: '{input_text}'")
    
    # 初始化管理器
    manager = HotwordManager()
    manager.load_all()
    
    # 1. 音素替换
    correction_result = manager.get_phoneme_corrector().correct(input_text, k=10)
    text = input_text
    if Config.hot:
        text = correction_result.text
        print(f"音素替换后: '{text}'")
        if correction_result.matchs:
            print(f"匹配详情: {correction_result.matchs}")
        else:
            print("未匹配到任何热词")
            # 打印所有热词以确认加载
            # print(f"当前热词库: {list(manager.get_phoneme_corrector().hotwords.keys())}")
    else:
        print("Config.hot 为 False，跳过音素替换")

    # 2. 去标点
    text = strip_punc(text)
    print(f"去标点后: '{text}'")

    # 3. 规则替换
    text = manager.get_rule_corrector().substitute(text)
    print(f"规则替换后: '{text}'")
    
    # 最终结果
    print(f"\n最终结果: '{text}'")
    
    if text != input_text:
        print("成功：文本已发生变化。")
    else:
        print("警告：文本未发生变化！可能原因：热词未生效或分数不足。")

if __name__ == "__main__":
    test_hotword_replacement()

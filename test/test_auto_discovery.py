import sys
import os
# 添加项目根目录到 sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from util.qwen_asr_gguf.inference.encoder import find_dml_device_id
from util.qwen_asr_gguf.inference.asr import find_vulkan_device_id

def test_device_discovery():
    print("=== 测试设备自动发现逻辑 ===")
    
    print("\n--- DirectML (Encoder) ---")
    nvidia_dml = find_dml_device_id("NVIDIA")
    print(f"查找 'NVIDIA': ID {nvidia_dml}")
    
    intel_dml = find_dml_device_id("Intel")
    print(f"查找 'Intel': ID {intel_dml}")
    
    print("\n--- Vulkan (Decoder) ---")
    nvidia_vk = find_vulkan_device_id("NVIDIA")
    print(f"查找 'NVIDIA': ID {nvidia_vk}")
    
    intel_vk = find_vulkan_device_id("Intel")
    print(f"查找 'Intel': ID {intel_vk}")

if __name__ == "__main__":
    test_device_discovery()

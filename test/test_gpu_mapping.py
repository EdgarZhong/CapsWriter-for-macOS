import subprocess
import sys
import os

def get_gpu_info_wmic():
    print("--- Windows GPU Info (WMIC) ---")
    try:
        # get caption (name), pnpdeviceid, and deviceid (often corresponds to enum order)
        cmd = "wmic path win32_videocontroller get caption, deviceid, pnpdeviceid, adapterram /format:list"
        result = subprocess.check_output(cmd, shell=True, text=True)
        print(result)
    except Exception as e:
        print(f"Error running wmic: {e}")

def check_onnx_providers():
    print("\n--- ONNX Runtime Providers ---")
    try:
        import onnxruntime as ort
        print(f"Available Providers: {ort.get_available_providers()}")
        
        # Try to infer device ID mapping if DmlExecutionProvider is present
        if 'DmlExecutionProvider' in ort.get_available_providers():
            print("DmlExecutionProvider is available. It typically follows the DXGI adapter order.")
            # Note: We can't easily verify the mapping without loading a model, 
            # but the WMIC order usually matches.
    except ImportError:
        print("onnxruntime not installed.")

def check_vulkan_info():
    print("\n--- Vulkan Info ---")
    try:
        # Try running vulkaninfo
        # We only want summary info
        cmd = "vulkaninfo --summary" 
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True)
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            print("vulkaninfo command failed. Trying without --summary...")
            cmd = "vulkaninfo"
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True)
            stdout, stderr = process.communicate()

        if process.returncode != 0:
            print("vulkaninfo command failed or not found.")
            return

        print("Vulkan devices found:")
        lines = stdout.splitlines()
        
        # Simple parsing for device info
        for line in lines:
            line = line.strip()
            # Different versions of vulkaninfo have different output formats
            if "deviceName" in line or "GPU id" in line or "deviceType" in line:
                 print(line)
            # Catch summary format
            if "GPU" in line and ":" in line:
                print(line)
                
    except Exception as e:
        print(f"Error checking vulkan info: {e}")

if __name__ == "__main__":
    get_gpu_info_wmic()
    check_onnx_providers()
    check_vulkan_info()

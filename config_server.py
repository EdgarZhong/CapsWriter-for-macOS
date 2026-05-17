import os
import sys
from pathlib import Path

# 版本信息
__version__ = '2.5'

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# 服务端配置
class ServerConfig:
    addr = '0.0.0.0'
    port = '6016'

    # 语音模型选择：
    # - Windows 继续沿用现有 qwen_asr(=GGUF) 路线，避免影响当前稳定基线。
    # - macOS 默认切到 qwen_asr_mlx，优先服务 Apple Silicon 的常驻低功耗场景。
    # - 仍保留 sensevoice / paraformer / fun_asr_nano 的显式配置入口，避免破坏现有兼容性。
    model_type = 'qwen_asr_mlx' if sys.platform == 'darwin' else 'qwen_asr'

    format_num = True       # 输出时是否将中文数字转为阿拉伯数字
    format_spell = True     # 输出时是否调整中英之间的空格

    enable_tray = True        # 是否启用托盘图标功能
    hotwords_path = Path() / 'hot-server.txt' # 全局热词配置文件路径

    # 日志配置
    log_level = 'DEBUG'        # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    aligner_idle_timeout = 10  # 对齐引擎空闲多少秒后自动释放显存 (0 表示不释放)

    # 集成显卡兼容性补丁
    # os.environ["GGML_VK_DISABLE_COOPMAT"] = "1"   # AMD集显无法加载 GGUF 模型时尝试
    # os.environ["GGML_VK_DISABLE_F16"] = "1"       # 集成显卡解码有误，强制熔断时尝试




class ModelDownloadLinks:
    """模型下载链接配置"""
    # 统一导向 GitHub Release 模型页面
    models_page = "https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models"


class ModelPaths:
    """模型文件路径配置"""

    # 基础目录
    model_dir = Path() / 'models'

    # Paraformer 模型路径
    paraformer_dir = model_dir / 'Paraformer' / "speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx"
    paraformer_model = paraformer_dir / 'model.onnx'
    paraformer_tokens = paraformer_dir / 'tokens.txt'

    # 标点模型路径
    punc_model_dir = model_dir / 'Punct-CT-Transformer' / 'sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12' / 'model.onnx'

    # SenseVoice 模型路径，自带标点
    sensevoice_dir = model_dir / 'SenseVoice-Small' / 'Sensevoice-Small-ONNX'
    sensevoice_encoder = sensevoice_dir / 'SenseVoice-Encoder.fp16.onnx'
    sensevoice_decoder = sensevoice_dir / 'SenseVoice-CTC.fp16.onnx'
    sensevoice_tokenizer = sensevoice_dir / 'tokenizer.bpe.model'


    # Fun-ASR-Nano 模型路径，自带标点
    fun_asr_nano_gguf_dir = model_dir / 'Fun-ASR-Nano' / 'Fun-ASR-Nano-GGUF'
    fun_asr_nano_gguf_encoder_adaptor = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx'
    fun_asr_nano_gguf_ctc = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-CTC.fp16.onnx'
    fun_asr_nano_gguf_llm_decode = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-Decoder.q5_k.gguf'
    fun_asr_nano_gguf_token = fun_asr_nano_gguf_dir / 'tokens.txt'
    fun_asr_nano_gguf_hotwords = Path() / 'hot-server.txt'

    # Qwen3-ASR 模型路径，自带标点
    qwen3_asr_gguf_dir = model_dir / 'Qwen3-ASR' / 'Qwen3-ASR-1.7B'
    qwen3_asr_gguf_encoder_frontend = qwen3_asr_gguf_dir / 'qwen3_asr_encoder_frontend.onnx'
    qwen3_asr_gguf_encoder_backend = qwen3_asr_gguf_dir / 'qwen3_asr_encoder_backend.onnx'
    qwen3_asr_gguf_llm_decode = qwen3_asr_gguf_dir / 'qwen3_asr_llm.gguf'

    # macOS / MLX 路线的本地模型目录约定。
    # 当前已根据用户新口径切换为“默认优先 8bit，本地缺失时再降级到 4bit”。
    # 这样做有两个目的：
    # 1. 在本机已准备好 8bit 权重时，直接命中更高质量的默认规格。
    # 2. 在 8bit 尚未就绪时，仍保留 4bit 和远端仓库作为启动兜底，避免本地开发被卡死。
    qwen3_asr_mlx_8bit_dir = model_dir / 'Qwen3-ASR-MLX' / 'Qwen3-ASR-1.7B-8bit'
    qwen3_asr_mlx_4bit_dir = model_dir / 'Qwen3-ASR-MLX' / 'Qwen3-ASR-1.7B-4bit'

    # Force-Aligner 模型路径
    force_aligner_gguf_dir = model_dir / 'Qwen3-ForcedAligner' / 'Qwen3-ForcedAligner-0.6B'
    force_aligner_gguf_encoder_frontend = force_aligner_gguf_dir / 'qwen3_aligner_encoder_frontend.int4.onnx'
    force_aligner_gguf_encoder_backend = force_aligner_gguf_dir / 'qwen3_aligner_encoder_backend.int4.onnx'
    force_aligner_gguf_llm_decode = force_aligner_gguf_dir / 'qwen3_aligner_llm.q5_k.gguf'

    @staticmethod
    def resolve_qwen3_asr_mlx_model() -> str:
        """
        解析 MLX 模型入口。

        设计意图：
        1. 默认优先使用本地 8bit 目录，匹配当前已确认的新默认规格。
        2. 若 8bit 缺失，则自动回退到本地 4bit，保证项目仍可离线启动。
        3. 若本地目录都未准备好，再回退到社区 4bit 仓库 ID，方便冷启动联调。
        4. 只把“目录非空”视为本地模型可用，避免把空目录误判成有效模型。
        """
        local_candidates = [
            ModelPaths.qwen3_asr_mlx_8bit_dir,
            ModelPaths.qwen3_asr_mlx_4bit_dir,
        ]
        for local_dir in local_candidates:
            if local_dir.exists() and any(local_dir.iterdir()):
                return local_dir.as_posix()
        return 'mlx-community/Qwen3-ASR-1.7B-4bit'



class ParaformerArgs:
    """Paraformer 模型参数配置"""

    paraformer = ModelPaths.paraformer_model.as_posix()
    tokens = ModelPaths.paraformer_tokens.as_posix()
    num_threads = 4
    sample_rate = 16000
    feature_dim = 80
    decoding_method = 'greedy_search'
    provider = 'cpu'
    debug = False


class SenseVoiceArgs:
    """SenseVoice 模型参数配置"""

    encoder_path = ModelPaths.sensevoice_encoder.as_posix()
    decoder_path = ModelPaths.sensevoice_decoder.as_posix()
    tokenizer_path = ModelPaths.sensevoice_tokenizer.as_posix()
    itn = True                  # 原生输出阿拉伯数字
    onnx_provider = 'CPU'       # ONNX 推理后端 (CPU, DML)
    top_k = 8                   # 热词检索的 CTC 空间大小
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果


class FunASRNanoGGUFArgs:
    """Fun-ASR-Nano-GGUF 模型参数配置"""

    # 模型路径
    encoder_onnx_path = ModelPaths.fun_asr_nano_gguf_encoder_adaptor.as_posix()
    ctc_onnx_path = ModelPaths.fun_asr_nano_gguf_ctc.as_posix()
    decoder_gguf_path = ModelPaths.fun_asr_nano_gguf_llm_decode.as_posix()
    tokens_path = ModelPaths.fun_asr_nano_gguf_token.as_posix()

    # 显卡加速
    onnx_provider = 'CPU'       # ONNX 推理后端 (CPU, DML)
    llm_use_gpu = True          # 是否启用 GPU 加速 GGUF 模型
    vulkan_force_fp32 = False   # 是否强制 FP32 计算（如果 GPU 是 Intel 集显且出现精度溢出，可设为 True）
    
    # 模型细节
    enable_ctc = True           # 是否启用 CTC 热词检索
    n_predict = 512             # LLM 最大生成 token 数
    n_threads = None            # 线程数，None 表示自动
    similar_threshold = 0.6     # 热词相似度阈值，超过阈值的热词会被传入 llm decoder 的上下文
    max_hotwords = 20           # 传入上下文的热词数量上限
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果
    verbose = False

class Qwen3ASRGGUFArgs:
    """Qwen3-ASR-GGUF 模型参数配置"""

    # 模型路径
    model_dir = ModelPaths.qwen3_asr_gguf_dir.as_posix()
    encoder_frontend_fn = ModelPaths.qwen3_asr_gguf_encoder_frontend.name
    encoder_backend_fn = ModelPaths.qwen3_asr_gguf_encoder_backend.name
    llm_fn = ModelPaths.qwen3_asr_gguf_llm_decode.name

    # 显卡加速
    onnx_provider = 'CPU'       # ONNX 推理后端 (CPU, DML)
    llm_use_gpu = True          # 是否启用 GPU 加速 GGUF 模型
    
    # 模型细节
    n_ctx = 2048                # 上下文窗口大小
    chunk_size = 80.0           # 分段长度（秒）
    memory_num = 1              # 记忆段数
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果
    verbose = False


class Qwen3ASRMLXArgs:
    """Qwen3-ASR-MLX 模型参数配置"""

    # 模型入口既支持本地目录，也支持 Hugging Face 仓库 ID。
    # 当前默认规格已经切到 1.7B-8bit，但仍保留 4bit 作为本地和远端回退选项。
    # 这样可以在不破坏启动稳健性的前提下，优先使用用户已经准备好的 8bit 权重。
    model = ModelPaths.resolve_qwen3_asr_mlx_model()

    # 返回时间戳会触发上游 forced aligner 流程，首版为了优先跑通最终结果闭环先关闭。
    # 文件转录如需更精确时间戳，后续可再按阶段单独启用和验证。
    return_timestamps = False

    # 先让上游库按音频长度自动推导生成长度，避免在首版把 token 上限调参写死。
    max_new_tokens = None

    # 首版不接 speculative decoding / draft model，先保留最小调用面。
    verbose = False


class ForceAlignerGGUFArgs:
    """Force-Aligner-GGUF 模型参数配置"""

    # 模型路径
    model_dir = ModelPaths.force_aligner_gguf_dir.as_posix()
    encoder_frontend_fn = ModelPaths.force_aligner_gguf_encoder_frontend.name
    encoder_backend_fn = ModelPaths.force_aligner_gguf_encoder_backend.name
    llm_fn = ModelPaths.force_aligner_gguf_llm_decode.name

    # 显卡加速
    onnx_provider = 'CPU'       # ONNX 推理后端 (CPU, DML)
    llm_use_gpu = False          # 是否启用 GPU 加速 GGUF 模型
    
    # 对齐细节
    n_ctx = 3072                # 上下文窗口大小
    dml_pad_to = 30             # 开启 DirectML 加速时，短音频统一填充到指定长度，有加速效果

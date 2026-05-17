# coding: utf-8
"""
模型检查模块

检查配置的语音模型文件是否存在，如果不存在则提供下载链接。
"""

import sys
from pathlib import Path

from config_server import ServerConfig as Config
from config_server import ModelPaths, ModelDownloadLinks
from core.server.state import console
from . import logger



def check_model() -> None:
    """
    根据配置的模型类型检查所需的模型文件是否存在
    
    如果模型文件不存在，显示错误信息和下载链接后退出程序。
    
    Raises:
        SystemExit: 当模型类型不支持或模型文件缺失时退出
    """
    model_type = Config.model_type.lower()
    logger.debug(f"检查模型文件, 类型: {model_type}")

    # 根据模型类型确定需要检查的文件
    if model_type == 'fun_asr_nano':
        required_files = {
            'Fun-ASR-Nano-GGUF 模型文件': [
                ModelPaths.fun_asr_nano_gguf_encoder_adaptor,
                ModelPaths.fun_asr_nano_gguf_ctc,
                ModelPaths.fun_asr_nano_gguf_llm_decode,
                ModelPaths.fun_asr_nano_gguf_token,
            ]
        }
    elif model_type == 'sensevoice':
        required_files = {
            'SenseVoice 模型文件': [
                ModelPaths.sensevoice_encoder,
                ModelPaths.sensevoice_decoder,
                ModelPaths.sensevoice_tokenizer,
            ]
        }
    elif model_type == 'paraformer':
        required_files = {
            'Paraformer 模型文件': [
                ModelPaths.paraformer_model,
                ModelPaths.paraformer_tokens,
            ],
            '标点模型文件': [
                ModelPaths.punc_model_dir,
            ]
        }
    elif model_type == 'qwen_asr':
        required_files = {
            'Qwen-ASR-GGUF 模型文件': [
                ModelPaths.qwen3_asr_gguf_encoder_frontend,
                ModelPaths.qwen3_asr_gguf_encoder_backend,
                ModelPaths.qwen3_asr_gguf_llm_decode,
            ]
        }
    elif model_type == 'qwen_asr_mlx':
        # MLX 路线的模型入口既可能是本地目录，也可能是远端 Hugging Face 仓库 ID。
        # 因此这里不能像 GGUF/ONNX 一样逐文件检查，而是要按“解析后的入口类型”分支处理：
        # 1. 若解析结果是本地目录，则检查目录存在且非空，避免把空目录误判成有效模型。
        # 2. 若解析结果不是本地目录格式，则视为远端仓库 ID，启动阶段允许继续，由上游库负责拉取/加载。
        resolved_model = ModelPaths.resolve_qwen3_asr_mlx_model()
        resolved_model_path = Path(resolved_model)
        if resolved_model_path.exists():
            required_files = {
                'Qwen-ASR-MLX 本地模型目录': [
                    resolved_model_path,
                ]
            }
        else:
            required_files = {}
    else:
        error_msg = f"不支持的模型类型: {Config.model_type}"
        logger.error(error_msg)
        console.print(f'''
    [bold red]不支持的模型类型：{Config.model_type}[/bold red]

    请在 config_server.py 中将 ServerConfig.model_type 设置为：
    - 'fun_asr_nano'
    - 'sensevoice'
    - 'paraformer'
    - 'qwen_asr'
    - 'qwen_asr_mlx'

        ''', style='bright_red')
        input('按回车退出')
        sys.exit(1)

    # 检查所有必需的文件
    missing_files = []
    for category, files in required_files.items():
        for file_path in files:
            # MLX 本地模型以目录为单位管理，所以除了存在性之外还要额外检查目录非空。
            # 这样可以把“目录名存在但权重未下载完成”的情况提前拦截出来。
            if not file_path.exists():
                missing_files.append((category, file_path))
                logger.warning(f"模型文件缺失: {file_path}")
            elif file_path.is_dir() and not any(file_path.iterdir()):
                missing_files.append((category, file_path))
                logger.warning(f"模型目录为空: {file_path}")

    # 如果有缺失的文件，显示错误信息并提供下载链接
    if missing_files:
        error_msg = f'\n    [bold red]未能找到模型文件[/bold red]\n\n'
        for category, file_path in missing_files:
            error_msg += f'    [{category}]\n'
            error_msg += f'    未找到：{file_path}\n\n'

        error_msg += f'    当前配置的模型类型：[bold yellow]{model_type}[/bold yellow]\n\n'

        # 提供统一下载页面链接
        error_msg += f'    [cyan]请前往模型发布页下载缺失文件：[/cyan]\n'
        error_msg += f'    [cyan]{ModelDownloadLinks.models_page}[/cyan]\n\n'

        error_msg += f'    下载后请根据发布页说明，解压到：[cyan]{ModelPaths.model_dir}[/cyan]\n'
        error_msg += '    \n'
        
        logger.error(f"模型文件检查失败，共 {len(missing_files)} 个文件缺失")
        console.print(error_msg)
        input('按回车退出')
        sys.exit(1)

    # 所有检查通过
    logger.info(f"模型文件检查通过 ({model_type})")
    console.print(f'[green4]模型文件检查通过 ({model_type})', end='\n\n')

# CapsWriter Tech ASR Eval v1 数据集落地说明

本目录用于建立第一轮 Qwen3-ASR 调优评测集的本地数据源、manifest 和派生样本。当前阶段只下载 v1 需要的最小源文件，不下载公开数据集全量。

## 数据源下载策略

| 模块 | 数据源 | 本轮下载范围 |
|------|--------|--------------|
| 中文真实低语 | `SMIIP-lab/AISHELL6-Whisper` | 元数据 + `test.tar.gz`，不下载 train/valid 全量 |
| 中文技术讲解 | `BAAI/Chinese-LiPS` | 元数据 + `processed_val.zip`，优先抽 `KJ` 科技主题 |
| 英文技术词汇 | `danielrosehill/Tech-Sentences-For-ASR-Training` | 小型仓库完整下载，保留音频和文本 |
| 英文技术演讲 | `AudioLLMs/tedlium3_test` | 单个 test parquet shard，后续只抽 20 条 |
| 商业技术口语 | `distil-whisper/earnings22` | 单个较小 chunked parquet shard，后续只抽 10 条 |

英文真实低语数据源 `wTIMIT` / `CHAINS` 暂不纳入首轮自动下载；若后续授权和入口确认，再单独补充。

## 使用方式

先确认已经登录 Hugging Face CLI，并且 gated 数据集已在网页侧获得访问权限：

```bash
hf auth whoami
```

若使用 Hugging Face fine-grained token，还需要在 token 设置中开启：
`Read access to contents of all public gated repos you can access`。
否则即使网页侧访问申请已通过，CLI 仍会对 `AISHELL6-Whisper` 返回 403。

启动下载：

```bash
.venv/bin/python evals/datasets/capswriter_tech_asr_v1/download_sources.py
```

后台下载并写日志：

```bash
nohup .venv/bin/python evals/datasets/capswriter_tech_asr_v1/download_sources.py \
  > evals/datasets/capswriter_tech_asr_v1/tmp/download_sources.nohup.log 2>&1 &
```

查看状态：

```bash
.venv/bin/python evals/datasets/capswriter_tech_asr_v1/download_sources.py --status
```

如果 `test.tar.gz` 通过 Hugging Face Xet 下载长时间无进展，可临时禁用 Xet 重跑，脚本会继续跳过已完成文件：

```bash
HF_HUB_DISABLE_XET=1 .venv/bin/python evals/datasets/capswriter_tech_asr_v1/download_sources.py --core-only
```

## 当前本机落地状态

- 2026-07-08：v1 所需最小源文件已下载完成，`download_sources.py --status` 返回 `complete`，失败项为空。
- `AISHELL6-Whisper` 已补齐元数据和 `test.tar.gz`，可进入中文真实低语抽样。

## 本地文件约定

- `sources/`：公开数据集源文件和最小 shard，本地缓存，不提交 Git。
- `raw/`：后续解压和抽样后的原始评测音频，本地缓存，不提交 Git。
- `manifests/`：后续提交固定 manifest、字段说明和小型索引。
- `tmp/`：下载日志、状态文件和临时输出，本地缓存，不提交 Git。

大型源文件只保存在本机，后续 Git 只提交脚本、manifest、下载说明和必要的小型元数据。

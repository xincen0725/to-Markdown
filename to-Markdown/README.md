# to-Markdown

> 多源输入转结构化 Markdown 笔记，专为知识管理设计。

## 概述

to-Markdown 是一个智能笔记工具，能够将 **PDF、视频、网页、音频** 转化为结构化的 Markdown 笔记。支持断点续传、批量处理、Obsidian 集成，并可作为 MCP Server 供 AI 工具调用。

## 功能

| 功能 | 描述 |
|------|------|
| 📄 PDF 转笔记 | 单个/批量，OCR（繁体竖排），断点续传 |
| 📋 SOP 提取 | 自动识别步骤、决策点，生成 Mermaid 流程图 |
| 🎬 视频转笔记 | YouTube/Bilibili/网页视频，字幕+转录 |
| 🎵 音频转笔记 | mp3/wav/m4a/ogg/flac/wma，批量处理 |
| 🌐 网页转笔记 | 正文提取，结构化总结 |
| 🔄 断点续传 | 中断后自动跳过已完成项 |
| 📦 批量处理 | 并发控制，独立失败不影响整体 |
| 📓 Obsidian 集成 | 自动保存到仓库，frontmatter + Wikilinks |
| 🔌 MCP Server | 作为 AI 工具直接调用 |

## 安装

**技能内置依赖管理器**——无需手动 `pip install`。

```bash
# 克隆技能到 skills 目录即可使用
# 首次运行自动安装所需依赖，开箱即用
```

### 国内用户：配置 pip 镜像源（推荐）

Python 包默认从官方 PyPI 下载，国内速度较慢。建议配置国内镜像：

```bash
# 清华源（推荐）
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 阿里源
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 腾讯源
pip config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple

# 仅本次生效（不修改全局配置）
pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple <package>
```

### 依赖管理机制

| 层级 | 依赖 | 安装方式 |
|------|------|---------|
| **核心层** | Schema、状态机、断点续传、重试引擎 | **零外部依赖**，纯 Python 标准库 |
| **PDF** | pymupdf | 首次处理 PDF 时自动安装 |
| **OCR** | pytesseract, opencv-python, Pillow | 启用 OCR 时自动安装 |
| **视频** | yt-dlp | 处理视频时自动安装 |
| **网页** | httpx, beautifulsoup4, lxml | 处理网页时自动安装 |
| **AI** | openai | 转录/总结时自动安装 |
| **CLI** | typer, rich | CLI 入口自动安装 |
| **MCP** | mcp | MCP Server 启动时自动安装 |

> 核心架构（`schemas/` + `core/`）完全基于 Python 标准库，无需任何 pip 包即可运行 Schema 校验、状态管理、断点续传和重试引擎。

### 外部系统依赖（仅需安装一次，不支持自动安装）

- **Tesseract OCR**（可选，OCR 功能需要）
  - macOS: `brew install tesseract`
  - Ubuntu: `apt install tesseract-ocr`
  - Windows: [下载安装包](https://github.com/UB-Mannheim/tesseract/wiki)
- **FFmpeg**（可选，音视频处理需要）
  - macOS: `brew install ffmpeg`
  - Ubuntu: `apt install ffmpeg`
  - Windows: [从 ffmpeg.org 下载](https://ffmpeg.org/download.html)，解压后将 `bin` 目录添加到系统 PATH

## 快速开始

### CLI

```bash
# PDF 转笔记
to-markdown pdf ./document.pdf

# 批量处理文件夹
to-markdown pdf ./pdf_folder/ --source folder

# 启用 OCR（繁体竖排）
to-markdown pdf ./ancient_book.pdf --ocr --ocr-dir vertical

# YouTube 视频转笔记
to-markdown video "https://youtube.com/watch?v=xxx"

# Bilibili 视频转笔记
to-markdown video "https://bilibili.com/video/BVxxx" --source bilibili

# 音频转笔记
to-markdown audio ./lecture.mp3

# 网页转笔记
to-markdown web "https://example.com/article"

# SOP 提取
to-markdown sop ./manual.pdf

# 强制重新处理
to-markdown pdf ./doc.pdf --force

# 指定输出目录
to-markdown pdf ./doc.pdf --output-dir ./my_notes
```

### Python API

```python
import asyncio
from src import ToMarkdown

async def main():
    tm = ToMarkdown(output_dir="./output")

    # PDF 转笔记
    result = await tm.pdf_to_note(
        path="./document.pdf",
        ocr_enabled=True,
        ocr_direction="auto",  # 自动判断横排/竖排
    )
    if result.is_success:
        print(f"输出: {result.data.output_path}")

    # 视频转笔记
    result = await tm.video_to_note(
        url="https://youtube.com/watch?v=xxx",
        source="youtube",
    )

    # 网页转笔记
    result = await tm.web_to_note(
        url="https://example.com/article",
        extract_main=True,
    )

    # SOP 提取
    result = await tm.extract_sop(path="./manual.pdf")

asyncio.run(main())
```

### MCP Server

```json
{
  "mcpServers": {
    "to-markdown": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "env": {
        "OUTPUT_DIR": "./notes",
        "CHECKPOINT_DIR": ".checkpoints"
      }
    }
  }
}
```

## 配置

### 默认配置

```yaml
# skill.yaml
config:
  output_dir: "./output"
  checkpoint_dir: ".checkpoints"
  max_concurrency: 4
  retry:
    max_retries: 3
    base_delay_seconds: 1.0
    max_delay_seconds: 60.0
    timeout_seconds: 300.0
  ocr:
    language: "chi_tra+chi_sim+eng"
    dpi: 300
    direction: "auto"
```

## 架构

详见 [ARCHITECTURE.md](ARCHITECTURE.md)

### 核心设计原则

1. **幂等性**: 同一输入多次执行结果一致，基于 SHA256 的 checkpoint 机制
2. **自愈性**: 指数退避重试 + 全局超时熔断
3. **隔离性**: 模块间通过防腐层通信，InternalChunk 标准化传递
4. **可观测性**: 每步状态追踪，结构化错误信息
5. **契约驱动**: Pydantic v2 严格类型校验

### 状态机

```
IDLE → VALIDATED → RUNNING → CHUNKING → MERGING → COMPLETED
                         ↘     ↘          ↘
                          FAILED ←──────────┘
                            ↓ (retry < max)
                          RUNNING
```

### 断点续传

```
.checkpoints/
├── pdf_to_note/
│   └── {input_sha256}.json    ← 每个任务独立 checkpoint
├── sop_extract/
├── video_to_note/
├── audio_to_note/
└── web_to_note/
```

## Obsidian 集成

```python
from src.integrations import ObsidianIntegration

obsidian = ObsidianIntegration(
    vault_path="/path/to/obsidian/vault",
    default_subdir="notes",
)

# 保存笔记
obsidian.save_note(note, tags=["learning", "ai"])

# 创建索引页 (MOC)
obsidian.create_index(notes, index_name="学习笔记索引")
```

## 支持格式

### 输入
- PDF (.pdf)
- 视频: YouTube, Bilibili, 任意网页视频, 本地文件
- 音频: mp3, wav, m4a, ogg, flac, wma
- 网页: 任意 URL

### 输出
- Markdown (.md)
- Obsidian 格式 (含 frontmatter + Wikilinks)

## 错误处理

所有错误通过统一的 `Result[T]` 封装：

```python
Result.success(data)        # 完全成功
Result.failure(error)       # 完全失败
Result.partial(data, errors) # 部分成功
```

错误分类：
- `retryable`: 网络超时、临时故障 → 自动重试
- `non_retryable`: 输入错误、权限不足 → 立即失败
- `degraded`: 部分成功 → 返回可用数据 + 错误列表

## AI Agent 使用指南

本技能专为 AI Agent（如 CodeBuddy、Claude、GPT）设计，可通过自然语言直接调用。

### 对话式调用

直接对 Agent 说出需求即可：

```
"帮我把这个 PDF 转成 Markdown 笔记"
"把 /path/to/report.pdf 转成结构化笔记，启用 OCR"
"分析这个 SOP 文档，提取操作流程"
"总结这个 YouTube 视频的内容 https://youtube.com/watch?v=xxx"
"把这篇网页转成笔记 https://example.com/article"
"把 /path/to/audio.mp3 转录并总结成笔记"
"批量处理 /path/to/pdfs/ 文件夹下所有 PDF"
```

### Agent 可调用的能力

| 能力 | 触发词示例 |
|------|-----------|
| PDF 转笔记 | "转PDF"、"PDF笔记"、"提取PDF内容" |
| SOP 提取 | "提取SOP"、"分析流程"、"操作步骤" |
| 视频转笔记 | "视频总结"、"YouTube笔记"、"B站总结" |
| 音频转笔记 | "音频转录"、"录音笔记"、"转文字" |
| 网页转笔记 | "网页笔记"、"文章总结"、"抓取网页" |
| 批量处理 | "批量转换"、"处理文件夹"、"全部PDF" |
| 断点续传 | 自动生效，中断后重新运行相同命令即可 |

### Agent 调用示例

```python
# Agent 内部通过 Python API 调用
from src import get_api
tm = get_api()(output_dir="./notes")

# PDF 转笔记
result = await tm.pdf_to_note(path="./doc.pdf", ocr_enabled=True)

# 视频转笔记
result = await tm.video_to_note(url="https://youtube.com/watch?v=xxx")

# 网页转笔记
result = await tm.web_to_note(url="https://example.com/article")

# 检查结果
if result.is_success:
    print(f"输出: {result.data.output_path}")
```

### 输出位置

- 默认输出到 `./output/` 目录
- 可指定输出目录：Agent 会自动创建
- 断点续传数据存储在 `.checkpoints/`（30天自动清理）

## License

MIT

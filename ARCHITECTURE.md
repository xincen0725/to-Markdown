# to-Markdown 架构设计文档

> 版本: V1.0.0 | 最后更新: 2026-07-10

## 设计原则

```
┌─────────────────────────────────────────────────────────────┐
│                    设计原则金字塔                              │
│                    ┌──────────┐                               │
│                    │ 幂等性   │  ← SHA256 checkpoint         │
│                    ├──────────┤                               │
│                    │ 自愈性   │  ← RetryEngine + CircuitBreaker│
│                    ├──────────┤                               │
│                    │ 隔离性   │  ← 4层防腐层 + 纯逻辑模块分离   │
│                    ├──────────┤                               │
│                    │ 可观测性 │  ← 结构化 logging             │
│                    ├──────────┤                               │
│                    │ 契约驱动 │  ← Pydantic v2 + TypedDict    │
│                    └──────────┘                               │
└─────────────────────────────────────────────────────────────┘
```

## 模块分层架构

```
┌──────────────────────────────────────────────────────────┐
│                    外部输入 (CLI / MCP / API)               │
└────────────────────────┬─────────────────────────────────┘
                         │
    ┌────────────────────┼────────────────────┐
    ▼                    ▼                    ▼
┌─────────┐      ┌─────────────┐      ┌──────────┐
│ Pydantic│      │TaskValidator│      │ Pipeline │
│ v2      │ ───► │ (防腐层2)    │ ───► │ (编排层)  │
│(防腐层1)│      │ 业务规则校验  │      │ try/ex/  │
│类型校验  │      │ 文件/权限检查 │      │ finally  │
└─────────┘      └─────────────┘      └────┬─────┘
                                           │
              ┌────────────────────────────┼────────────────┐
              ▼                            ▼                ▼
     ┌──────────────┐            ┌──────────────┐  ┌──────────────┐
     │PDFExtractor  │            │OCREngine     │  │SOPAnalyzer   │
     │(纯逻辑·零副作用)│           │(纯逻辑·零副作用)│  │(纯逻辑·零副作用)│
     └──────┬───────┘            └──────┬───────┘  └──────┬───────┘
            │                           │                 │
            └───────────┬───────────────┴────────┬────────┘
                        ▼                        ▼
                 ┌──────────────┐         ┌──────────────┐
                 │PDFProcessor  │         │SOPProcessor  │  Video/Audio/
                 │(编排+副作用)  │         │(编排+副作用)  │  Web Processor
                 └──────┬───────┘         └──────┬───────┘
                        │                        │
                        └───────────┬────────────┘
                                    ▼
                         ┌──────────────────┐
                         │   Result[T]      │ ← 统一输出 (防腐层4)
                         │ success/failure/ │
                         │ partial          │
                         └──────────────────┘
```

## 状态机

```
                 ┌──────────┐
                 │  IDLE    │
                 └────┬─────┘
                      │
                 ┌────▼─────┐
                 │ VALIDATED│──── 校验失败 ────► FAILED
                 └────┬─────┘
                      │
                 ┌────▼─────┐
          ┌──────│ RUNNING  │──────┐
          │      └────┬─────┘      │
          │           │            │
     ┌────▼────┐ ┌───▼────┐  ┌────▼────┐
     │PAUSED  │ │CHUNKING│  │ FAILED  │
     └────┬────┘ └───┬────┘  └────┬────┘
          │           │            │
          └───────────┤            │ retry<max
                      │            │
                 ┌────▼─────┐      │
                 │MERGING   │      │
                 └────┬─────┘      │
                      │            │
                 ┌────▼─────┐      │
                 │COMPLETED │◄─────┘
                 └──────────┘

  Pipeline finally 兜底: 非终态 → 强制标记 FAILED
```

### 状态转换规则

| 当前状态 | 允许转换到 | 触发条件 |
|---------|-----------|---------|
| IDLE | VALIDATED | 提交任务 |
| VALIDATED | RUNNING | 校验通过 |
| VALIDATED | FAILED | 校验失败 |
| RUNNING | CHUNKING | 开始分块处理 |
| RUNNING | FAILED | 处理异常 |
| CHUNKING | MERGING | 所有分块完成 |
| CHUNKING | FAILED | 分块处理异常 |
| MERGING | COMPLETED | 合并成功 |
| MERGING | FAILED | 合并异常 |
| FAILED | RUNNING | 重试 |
| COMPLETED | — | 终态 |

## 四层防腐层

| 层级 | 组件 | 职责 |
|------|------|------|
| 第1层 | Pydantic v2 Schema | 类型/范围/格式严格校验 |
| 第2层 | TaskValidator | 业务规则校验（文件存在性、权限、依赖） |
| 第3层 | Pipeline (try/except/finally) | 编排隔离 + 状态机兜底 |
| 第4层 | Result[T] 泛型 | 统一 success/failure/partial 输出 |

## 核心机制

### Pipeline 三层异常保障

```
try:
    result = await processor.process(task, request)
    if success: mark_completed + transition(COMPLETED)
    elif failure: mark_failed + transition(FAILED)
except Exception:
    mark_failed + transition(FAILED)
finally:
    if state not in (COMPLETED, FAILED):
        force mark_failed + transition(FAILED)  # 兜底
    cleanup_expired()  # 每10次执行
```

### 幂等性

- 输入 SHA256 = 任务唯一标识
- checkpoint: `.checkpoints/{task_type}/{input_sha256}.json`
- 分块处理: `chunks_completed` 断点续传
- `--force` 强制重新处理
- `schema_version = 1` 格式版本化

### 依赖注入

```
ProcessorFactory.create(task_type)
  → 实例化 Processor
  → 注入共享 CheckpointManager (覆盖 __init__ 默认值)
  → Processor 不持有独立 checkpoint 实例
```

### 事务管理

```
UnitOfWork(checkpoint)
  .register(forward_op, rollback_op)
  .commit()   → 清空回滚栈
  .rollback() → 逆序执行回滚操作
```

### 懒加载

`_lazy.py` 自包含依赖映射，首次使用时自动 `pip install`：
- `fitz` → pymupdf
- `PIL.Image` → Pillow
- `pytesseract` → pytesseract
- `httpx` → httpx
- `bs4` → beautifulsoup4 + lxml

### 自动清理

`CheckpointManager.cleanup_expired()` 每10次 Pipeline 执行自动触发，删除超过30天的过期 checkpoint。

## 防多米诺骨牌分析

| 风险 | 防护机制 |
|------|---------|
| 批量处理中途崩溃 | 每个文件独立 InternalTask |
| 网络超时 | RetryEngine 指数退避 + CircuitBreaker 熔断 |
| 输入格式错误 | Pydantic 防腐层第1层拒绝，fail-fast |
| 模块间数据污染 | InternalChunk/ChunkMetadata 标准化传递 |
| 并发竞态 | 跨平台文件锁 + 单写者状态机 |
| 状态机残留 | Pipeline finally 兜底强制标记 FAILED |
| checkpoint 无限增长 | cleanup_expired 自动清理 |
| 注入覆盖 | ProcessorFactory 在 __init__ 之后注入 |
| 资源泄漏 | 所有 fitz.open() 有 finally close() |

## 目录结构

```
src/
├── core/
│   ├── checkpoint.py      # 断点续传 + 自动清理
│   ├── pipeline.py        # 编排器 (try/except/finally)
│   ├── state_machine.py   # 状态机
│   ├── retry.py           # 重试引擎 + 熔断器
│   ├── anticorruption.py  # 防腐层第2层
│   ├── unit_of_work.py    # 事务边界管理
│   ├── task_context.py    # 任务上下文容器
│   └── logging.py         # 结构化日志
├── processors/
│   ├── base.py            # 处理器基类 (公共方法)
│   ├── pdf_extractor.py   # PDFExtractor + OCREngine (纯逻辑)
│   ├── sop_analyzer.py    # SOPAnalyzer (纯逻辑)
│   ├── pdf_processor.py   # PDF 处理器
│   ├── sop_processor.py   # SOP 处理器
│   ├── video_processor.py # 视频处理器
│   ├── audio_processor.py # 音频处理器
│   ├── web_processor.py   # 网页处理器
│   ├── _lazy.py           # 自包含懒加载
│   └── __init__.py        # ProcessorFactory
├── schemas/
│   ├── enums.py           # 枚举定义
│   ├── input.py           # Pydantic 输入 Schema
│   ├── output.py          # 统一输出 Result[T]
│   ├── task.py            # InternalTask/InternalChunk
│   └── contracts.py       # TypedDict 契约
├── integrations/
│   ├── obsidian.py        # Obsidian 仓库集成
│   ├── ocr.py             # OCR 引擎封装
│   ├── youtube.py         # YouTube API
│   └── bilibili.py        # Bilibili API
├── mcp/
│   └── server.py          # MCP Server
├── main.py                # CLI + Python API 入口
└── bootstrap.py           # 自举依赖管理器
```

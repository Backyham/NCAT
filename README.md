# NCAT: Nick 的计算机辅助翻译套件

NCAT 是本地优先的计算机辅助字幕翻译工具，提供 CLI 与本地 Web 编辑器。核心负责项目、媒体、转写、Segment/Cue、TM/术语、QA、ASS 和渲染；ASR/MT 是可替换后端。

当前状态：CLI、本地 API、Web 编辑器、Workspace/Project/Subproject、术语库、翻译记忆、QA、本地/云端 ASR/MT 均已可用。

## 环境要求

- Python **3.12+**
- `ffmpeg` / `ffprobe`（ingest、渲染、音频转换）
- Web 编辑器：`starlette`、`uvicorn`
- ASR/MT 模型与运行时独立部署，不随本包发布

## 安装

```bash
python -m pip install -e .
# Web 依赖
python -m pip install -e '.[web]'
```

CLI 本身只依赖标准库。

## CLI

```bash
python -m ncat --version
python -m ncat init <project> [--source video] [--source-lang en] [--target zh-Hans]
python -m ncat ingest <project> [--height 720] [--encoder NAME] [--force]
python -m ncat transcribe <project> [--backend NAME] [选项]
python -m ncat segment <project> --propose | --import FILE [--target FILE]
python -m ncat edit <project> --export FILE | --import FILE
python -m ncat edit <project> --split-cue CUE [--ratio 0.5]
python -m ncat align <project>
python -m ncat tm <project> --import SOURCE --import-target TARGET | --match TEXT
python -m ncat term <project> --import FILE | --match TEXT
python -m ncat qa <project>
python -m ncat corpus <project> --source FILE --target FILE | --ass FILE
python -m ncat translate <project> [--backend NAME] [--limit N]
python -m ncat style <project>
python -m ncat render <project> [--preset original|youtube_1080p|bilibili_2160p]
python -m ncat reindex <project> [--search TEXT]
python -m ncat serve --workspace ~/NCAT
python -m ncat status <project>
```

主要选项：

- `init`：`--name`、`--source`、`--source-lang`、`--target`、`--force`
- `ingest`：`--height`、`--encoder`、`--force`；复用匹配 checksum/height 的产物
- `transcribe`：`--backend`、`--model`、`--language`、`--option KEY=VALUE`
- `edit`：导出/导入 `source<TAB>target`，以及 cue 的 split/merge/insert/delete/lock/status
- `style`：`--template`、`--bilingual/--no-bilingual`
- `render`：`--encoder`、`--crf`、`--ass`
- `reindex`：重建 SQLite/FTS 索引；`--search` 可直接查询

除 `serve` 外，命令支持 `--json`；错误输出为结构化 JSON。

## 项目模型

项目是长期容器，包含子项目、术语库和翻译记忆：

```text
workspace/
  project.json                 # schema 2
  termbases/*.jsonl
  tm/*.jsonl
  subprojects/<name>/
    project.json               # 兼容旧版 schema 1
    media/source-720p.mp4
    media/source.wav
    asr/transcript.json
    asr/transcript.txt
    runs/<run_id>.json
    segments.jsonl
    cues.jsonl
    qa.jsonl
    index.db
    renders/
```

旧版单视频项目仍可直接打开，会被视为只有一个子项目的项目。

- Segment 是 TM/术语/MT 的单位；Cue 是 ASS/时间轴/CPS 的单位。
- TM/术语库可按项目级/子项目级导入；子项目覆盖项目级。
- 文件是事实来源；`index.db` 是可重建索引。

## Web 编辑器

```bash
python -m ncat serve --workspace ~/NCAT --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`。

- **Home**：NCAT 标题/副标题、New Project、最近项目。
- 标题栏：`Transcribe`、`Terms`、`TM`、`Settings`。
- 打开项目后的标签：`Assets`、`Editor`、`Transcript`、`QA`。
- `New Project` 选项在弹窗内；支持添加子项目。
- **Terms/TM** 不需要打开项目：可浏览全部数据，并按项目筛选。
- **Settings**：ASR/MT 的 local/cloud 模式，以及各自 API Key；不配置模型或 endpoint。
- **Health**：右上角气泡，显示 CPU/GPU/RAM/Disk 与 ASR/MT 服务状态。
- 编辑器支持双语编辑、时间编辑、CPS/行长提示、split/merge/delete、undo/redo、自动保存、波形、Transcript 审查与 QA/Punch list。

## ASR / MT

ASR 和 MT 都是可替换后端；核心不 import 模型运行时。

### 模式

- **local**：调用本地服务，不需要 API Key。
- **cloud**：调用云端 OpenAI-compatible 服务，使用对应服务的 API Key。

ASR 与 MT 的 Key 分开保存，不假设来自同一提供商。

CLI 继续使用显式 `--backend` / `--base-url` / `--option`；Web/API 使用 Settings 的
local/cloud 选择与各自 Key。

### 服务端配置

模型、endpoint、provider 属于后端服务配置，不在前端设置。

```text
NCAT_ASR_LOCAL_URL       默认 http://127.0.0.1:8081
NCAT_MT_LOCAL_URL        默认 http://127.0.0.1:8080
NCAT_MT_LOCAL_MODEL      本地 MT router 默认模型 ID
NCAT_ASR_CLOUD_URL
NCAT_ASR_CLOUD_MODEL
NCAT_MT_CLOUD_URL
NCAT_MT_CLOUD_MODEL
```

本地 ASR/MT 服务默认免鉴权；需要鉴权时在服务端启用并配置 Key。云端请求使用：

```http
Authorization: Bearer <key>
```

ASR 云端响应必须包含词级时间戳；只有纯文本的响应会被拒绝。

### 运行时模型管理

ASR 服务提供：

```text
GET  /v1/models
POST /v1/models/load
POST /v1/models/switch
POST /v1/models/unload
```

MT 本地服务使用 llama-server router 模式（`--models-dir`、`--models-max`、`--models-autoload`），可通过请求中的模型 ID 自动加载/卸载/切换。

### 自定义后端

CLI 通过 registry 加载自定义 ASR/MT 后端，核心不附带模型预设。

## transcript.json

`asr/transcript.json` 使用 `ncat.transcript/1`：

- `text`：完整转写文本
- `words[]`：`text`、`start_ms`、`end_ms`、可选 `confidence`，以及后端原生字段 `raw`
- `segments`：后端原生分段
- `chunks`：由 `--chunk-ms` 生成
- `metadata.asr_token_confidence`：后端提供的 token 级置信度
- 时间统一为绝对毫秒；`align` 使用词级时间

## 环境变量与输出

- `NCAT_WORKSPACE`：Web workspace 根目录
- `NCAT_ASR_PLUGINS` / `NCAT_MT_PLUGINS`：自定义后端插件
- `NCAT_ASR_*` / `NCAT_MT_*`：本地/云端 endpoint、模型与 Key
- `--json`：机器可读输出；错误包含 `error.code` / `error.message`

# NCAT: Nick 的计算机辅助翻译套件

NCAT 是本地优先的计算机辅助字幕翻译工具，提供 CLI 与本地 Web 编辑器。核心负责项目、媒体、转写、Segment/Cue、TM/术语、QA、ASS 和渲染；ASR/MT 是可替换后端。

当前状态：CLI、本地 API、Web 编辑器、Workspace/Project/Subproject、术语库、翻译记忆、QA、本地/云端 ASR/MT 均已可用。

## 环境要求

- Python **3.12+**
- `ffmpeg` / `ffprobe`（ingest、渲染、音频转换）
- Web 编辑器：`starlette`、`uvicorn`
- ASR/MT 模型与运行时独立部署，不随本包发布

开发环境为 **VS Code Remote-WSL**：仓库位于 WSL 的 `~/code/NCAT_R`，从 Windows 用 `code --remote wsl+archlinux /home/nick/code/NCAT_R` 打开；所有命令在 VS Code 集成终端（WSL）执行。

## 安装

```bash
python -m pip install -e .
# Web 依赖
python -m pip install -e '.[web]'
```

CLI 本身只依赖标准库。

服务器部署可直接使用 `ncat-server`（等价于 `python -m ncat.api`）；Web 前端不通过 CLI 执行项目动作，`ncat` 仅保留开发与运维命令。

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
  project.json                 # schema 3
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

- 项目 schema 3 增加分类/泳道、功能开关、自动化规则；子项目包含分类/泳道、子任务、关联、附件、评论、完成状态。
- Segment 是 TM/术语/MT 的单位；Cue 是 ASS/时间轴/CPS 的单位。
- TM/术语库可按项目级/子项目级导入；子项目覆盖项目级。
- 文件是事实来源；`index.db` 是可重建索引；项目活动写入 `activity.jsonl`。

## Web 编辑器

```bash
python -m ncat.api --workspace ~/NCAT --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`。

- **Home**：NCAT 标题/副标题、新建项目、最近项目。
- Header：左侧菜单图标 + NCAT 品牌；`转写`、`术语`、`记忆库`、`设置` 和运行状态移入左侧可开关侧拉栏，设置在底部，运行状态紧挨其上方；侧栏可固定，固定时保持展开、不显示遮罩，并为主内容预留侧栏宽度。
- 打开项目后的视图：`概览` / `看板` / `列表`，三按钮位于搜索框前；旧项目/子项目下拉、刷新、返回首页已移除。
- **概览**：项目统计、按分类/泳道统计任务数、最近动态（子任务完成/取消、泳道移动、分类/子项目/子任务增删）。
- **看板**：分类纵向可折叠；每个分类内泳道=用户自定义阶段列，横向排列；卡片=子项目，点击打开详情。所有移动手动拖拽（同一分类内）或项目自动化规则触发。
- **列表**：默认平铺所有子项目，可切 `分类 > 泳道 > 子项目` 层级目录树；搜索与筛选同时生效。
- **搜索筛选**：支持 `created>=`、`completed<=`、`due<=`、`due_within:7d`、`status:done|open`、`category:"名称"` 和自由文本；筛选按钮插入语法模板。
- **项目设置**：编辑信息（名称）、编辑看板（分类/泳道增删改名排序）、项目设置（功能开关与自动化规则）。
- **子项目详情**：点击卡片/列表进入；新建直接创建“新项目”并进入编辑状态。详情含名称/简介/分类/泳道/截止/完成、子任务 checklist、内部/外部关联、附件、评论、删除。
- 子项目工作区标签：`概览`、`导入`、`转录`、`机器翻译`、`文本编辑`、`字幕编辑`、`导出`。转录/机器翻译暂为“暂未实现”占位；现有 ASR/MT 动作保留在导入/文本编辑过渡。
- **功能开关**：媒体处理(ffmpeg)/ASR/分句/时轴/MT/字幕编辑/导出渲染可关闭；关闭后隐藏对应标签/动作，后端拒绝禁用动作。
- **编辑器字幕行**：左侧状态目录（空 / MT 建议 / TM 建议 / 已翻译 / 已审阅 / 已确认 / 已退回 / 绘图），点击子任务在右侧编辑。
- `新建项目` 可选择默认布局（默认分类+8泳道+全功能）或空项目（0 分类、功能关闭）；空项目需先在编辑看板创建分类/泳道才能新建子项目。
- **Terms/TM** 不需要打开项目：可浏览全部数据，并按项目筛选；记忆库标签支持全文检索与 TMX 导出。
- **Settings**：左侧“提供商”分类；ASR/MT 可配置 Provider ID/Name、API Type（ASR OpenAI Audio；MT OpenAI Chat/Responses/Anthropic）、Endpoint、API Key、Header 与默认模型。添加/修改通过弹窗完成，保存时按 Endpoint/Key 检查 API 并获取模型；默认模型为下拉列表（A–Z），末尾可选择“添加自定义模型”（填写 Model ID 与 Model Name；MT 还可标记 Reasoning / Image 能力）。保存后提供商以列表显示，可修改或删除；列表初始为空，本地服务也需手动添加。
- **Health**：侧栏“运行状态”气泡，只显示已配置 ASR/MT 提供商的 CPU、GPU、内存；指示灯在完全未配置/已忽略时为灰色，只配置一个时显示该服务状态，两个都配置时按不可达 > 过热 > 正常优先显示；`/health` 不可用时回退 `/v1/models` 并提示，可在状态卡片或提供商弹窗忽略检查；数据由各服务自己的 `/hardware` 端点采样，另附已加载/可用模型；面板打开时每 3 秒刷新。
- 编辑器支持双语编辑、时间编辑、CPS/行长提示、split/merge/delete、undo/redo、自动保存、波形、转写稿审查与 QA 待办。
- 编辑器 ASS 感知：override tag 以不可编辑 chip 呈现，`\N` 为换行 chip，绘图行只读且不进 MT/QA；锁定行拒绝编辑。
- 编辑器建议面板：按当前行查询记忆库与术语，显示匹配率并可一键插入；单行 MT 建议只展示、不写回。
- 段落状态：空 / 机器翻译建议 / 记忆库建议 / 已翻译 / 已审阅 / 已确认 / 已退回；确认后写入本机记忆库，相同原文的未确认行自动传播。
- 样式标签页：编辑 `assets/template.ass` 的样式表与脚本信息，查看当前行 override 标签与最近操作记录。

## ASR / MT

ASR 和 MT 都是可替换后端；核心不 import 模型运行时。MT 提示词按 Index 官方 instTrans 结构组装（源语言/目标语言/文体、`【源文】`、编号 `【硬性要求】/【注意】`、只输出译文的结尾指令），多行文本以 JSON 键值对传递以保留 Segment 对应关系。

### 提供商

ASR 与 MT 都没有默认提供商，Settings 的提供商列表初始为空。可以添加多个
ASR/MT 提供商（本地与云端是不同 provider），再为每个服务选择一个“当前提供商”；
本地服务也只是一个普通提供商（Endpoint 填本地地址），不会自动创建。

未选择当前提供商时，对应的转写/翻译动作返回 `provider_not_configured`。API Key
按 provider 分开保存；云端请求可使用 Bearer 或 Anthropic 的 `x-api-key` 头。

CLI 继续使用显式 `--backend` / `--base-url` / `--option`；Web/API 使用 Settings
里的提供商配置（Provider ID/Name、API Type、Endpoint、Key、Header、默认模型）。保存时会
检查 Endpoint/API 并尝试获取模型；未选择模型时自动选择首个结果，失败则提示手动添加。

### 运行时模型管理

ASR 服务提供：

```text
GET  /health
GET  /hardware          # 该服务的 CPU / GPU / 内存
GET  /v1/models
POST /v1/models/load
POST /v1/models/switch
POST /v1/models/unload
```

MT 本地服务由 `ncat.test.mt_server` 作为监督进程提供：llama-server 跑在内部回环端口，
对外端口转发全部 OpenAI 兼容请求，并额外提供 `GET /health`、`GET /hardware`；
模型仍通过 llama-server router 的模型 ID 自动加载/卸载/切换。

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

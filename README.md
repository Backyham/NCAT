# NCAT: Nick 的计算机辅助翻译套件

自托管的英译中字幕工具：转录、人工审阅断句、机器翻译、压制。
当前设计目标是一台专用机器上的单用户场景，因此界面保持简单，不追求多人协作。


## 功能

七个阶段串成一条流水线：

```
download → transcode → transcribe → review → segment → translate → style → render
```

| 阶段 | 作用 |
|---|---|
| `download` | 通过 yt-dlp 获取源媒体 |
| `transcode` | 720p 代理 + 16 kHz 单声道 WAV（`--audio-only` 可只取音频） |
| `transcribe` | Qwen3-ASR 转录 + Forced Aligner 给出词级时间轴 |
| `review` | 提出断句方案并写出草稿，**人工在此校对** |
| `segment` | 按人工确定的边界对齐词时间轴 |
| `translate` | 把**整段**审阅后的文本交给 MT，再人工切分回 cue |
| `style` | 输出双语 `.ass`（CRLF + BOM） |
| `render` | 烧录到原始分辨率视频（NVENC） |

## 硬件

| 组件 | 本机配置 |
|---|---|
| GPU | NVIDIA Tesla P40，Pascal，`sm_61`，24 GB |
| 驱动 | 580.xx 专有版 |
| CUDA | 12.9 |
| 操作系统 | Linux（Arch，已验证） |

### 约束

- **Pascal 没有 bf16 张量核心。** FP8 量化模型无法运行；约 54 GB 的 BF16 模型放不下。
- **torch 最后一个支持 `sm_61` 的 wheel 是 `2.6.0+cu118`。** 它的架构列表里写的是 `sm_60`，
  这是正确的 —— sm_60 的 cubin 与 sm_61 二进制兼容。
- **`torch.cuda.is_bf16_supported()` 在 Pascal 上返回错误的答案。** 因此 dtype 按算力
  能力选择，而不是问 torch。
- **llama.cpp 必须自行编译**，针对你自己的 GPU 架构。它是 MT 的唯一可行后端：vLLM 在
  `sm_61` 上根本无法运行。

---

## 安装

```bash
git clone <repo> && cd ncat

# 应用层（Python 3.14）
uv venv --python 3.14 .venv-app
uv pip install --python .venv-app/bin/python --group app
```

### ASR 层（Python 3.12）

torch 的构建与主环境不兼容，因此需要独立环境。这一层的安装依赖主机（CUDA 版本、wheel
可用性），故未写进 `pyproject.toml`：

```bash
uv venv --python 3.12 .venv-asr
uv pip install --python .venv-asr/bin/python \
    torch==2.6.0+cu118 --index-url https://download.pytorch.org/whl/cu118
uv pip install --python .venv-asr/bin/python \
    transformers accelerate librosa soundfile jiwer
```

### NLTK 数据（审阅阶段）

断句阶段使用 NLTK 的词性标注器。其模型位于 venv 之外，需要单独获取一次：

```bash
NLTK_ALLOW_PROXIED_URLOPEN=1 python -c \
  "import nltk; nltk.download('punkt_tab'); nltk.download('averaged_perceptron_tagger_eng')"
```

缺少它们时该阶段仍可运行，会退化为标点切分并打印警告。这里的环境变量开关只在配置了
代理时需要 —— NLTK 出于 SSRF 防护会拒绝经代理下载。

### MT（不需要虚拟环境）

针对你自己的架构编译 llama.cpp，`translate` 阶段会在 `.tools/llama-build/bin/llama-server`
找到它：

```bash
git clone https://github.com/ggml-org/llama.cpp .tools/llama.cpp
cmake -S .tools/llama.cpp -B .tools/llama-build -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=61 -DCMAKE_BUILD_TYPE=Release
cmake --build .tools/llama-build --config Release -j
```

## 运行

本项目**只提供 Web 服务**，没有命令行入口。每个阶段都会调用 ffmpeg，本来就属于
浏览器里的操作，再维护第二套入口只会让「流水线到底怎么跑」出现两个版本。

```bash
sudo systemctl enable --now ncat.socket     # 已在 http://<局域网IP>:80 上运行
uvicorn backend.app:app --reload            # 开发用，需先停掉 ncat.service
```

打开首页即可创建任务、查看阶段进度、下载产物。

### 部署到 80 端口

80 端口属于特权端口，绑定它需要 `CAP_NET_BIND_SERVICE`，而默认只有 root 拥有该能力。
**不要**这样做：

```bash
sudo setcap cap_net_bind_service=+ep .venv-app/bin/python   # 错误
```

该路径是指向 `/usr/bin/python3.14` 的符号链接，`setcap` 会跟随符号链接，于是能力会落到
系统解释器上，授予本机**所有** Python 程序而非仅仅 NCAT。

正确做法是由 systemd 以 root 完成绑定，再把套接字交给普通用户进程：

```bash
sudo cp deploy/ncat.socket deploy/ncat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ncat.socket
```

由此得到两个性质：即使服务未运行端口仍被占用，重启后无需登录即可恢复。
若没有 systemd，替代方案是在 venv 内放一份带 capability 的解释器副本，把权限限定在本项目，
但它需要额外的自启单元。

## 配置

仓库中没有任何硬编码路径。模型位置优先取自环境变量，其次是 `pyproject.toml` 的
`[tool.ncat]`：

```bash
export NCAT_ASR_MODEL=/path/to/Qwen3-ASR-1.7B-hf
export NCAT_ALIGNER_MODEL=/path/to/Qwen3-ForcedAligner-0.6B-hf
export NCAT_MT_MODEL=/path/to/Index-Translate-9B-F16.gguf
```

| 变量 | 含义 | 默认 |
|---|---|---|
| `NCAT_ASR_MODEL` | Qwen3-ASR 模型目录 | 未设置 |
| `NCAT_ALIGNER_MODEL` | 强制对齐器目录 | 未设置 |
| `NCAT_MT_MODEL` | MT `.gguf` | 未设置 |
| `NCAT_MT_<MODEL>_PATH` | 单个 MT 模型的路径，按注册名大写（例：`NCAT_MT_HY_MT2_7B_F16_PATH`） | 未设置 |
| `NCAT_LLM_MODEL` | 建议/自动补全模型 | 未设置 |
| `NCAT_MODELS_ROOT` | 模型根目录（便捷项） | 未设置 |
| `NCAT_DATA_DIR` | SQLite 与任务产物 | `./data` |
| `NCAT_ASR_PYTHON` | 含 ASR 栈的解释器（ASR 阶段据此委托给 `.venv-asr`） | 未设置 |
| `NCAT_FFMPEG` | 使用的 ffmpeg | `ffmpeg` |
| `NCAT_FFMPEG_FALLBACK` | 系统 ffmpeg 无可用硬件编码器时的备选 | 未设置 |

`llama-server` 的位置通过 `--server-bin` 传入，默认见 `pipeline/translate.py` 的解析顺序。

### 硬件编码

烧录是 4K60 的活，在 ffmpeg 用不上 GPU 的机器上非常昂贵。Tesla P40 上实测 10 秒 4K60：

| 编码 | 耗时 | 码率 | 40 分钟视频 |
|---|---|---|---|
| x264 `-preset slow` CRF 20 | 26.9 s | ~4.3 Mbps | ~107 分钟 |
| NVENC p4 @ 16 Mbps | 8.8 s | ~17 Mbps | ~35 分钟 |
| **NVENC p4 @ 25 Mbps**（默认） | 3.9 s | ~26 Mbps | **~16 分钟** |

NVENC p4 在画质相近的前提下快 3.4 倍。默认配置让整个烧录阶段只花 0.4× 实时。

### 断句方案的限制

`review` 阶段按 **75 字符**（英文文本）提出断句，同时尊重 21 cps 的阅读速度上限。
超过速度上限的 cue 会被**保留完整** —— 一个读得快的完整句子优于一个读得慢的碎片 ——
并报告出来而不是默默切开。这些方案是**建议**，人工可随意修改。

## 术语表

术语表与词条通过 API 提供，支持带 UTF-8 BOM 的 CSV 导出，以及「合并或替换」式导入
（返回 `added`/`updated`/`skipped` 计数）。

## HTTP API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 应用外壳 |
| GET | `/health` | 存活检查（仅说明后端在服务，不说明模型可用） |
| GET | `/api/models` | 哪些模型文件实际存在 |
| POST | `/api/jobs` | 创建并启动任务 |
| GET | `/api/jobs` | 近期任务 |
| GET | `/api/jobs/{job_id}` | 任务详情 |
| PATCH | `/api/jobs/{job_id}` | 修改任务 |
| POST | `/api/jobs/{job_id}/rerun` | 重跑，可指定阶段子集 |
| POST | `/api/jobs/{job_id}/cancel` | 请求取消 |
| DELETE | `/api/jobs/{job_id}` | 删除任务 |
| GET | `/api/jobs/{job_id}/cues` | 双语 cue 列表 |
| PATCH | `/api/jobs/{job_id}/cues/{cue_id}` | 编辑 cue |
| POST | `/api/jobs/{job_id}/cues/{cue_id}/split` | 在第 N 个词之前拆分 |
| POST | `/api/jobs/{job_id}/cues/{cue_id}/merge` | 与下一条合并 |
| GET | `/api/jobs/{job_id}/review` | 审阅数据（含逐 cue 词时间轴） |
| POST | `/api/jobs/{job_id}/review` | 提出断句方案并存为可编辑 cue |
| POST | `/api/jobs/{job_id}/review/approve` | 批准闸门 |
| GET | `/api/jobs/{job_id}/files` | 任务目录中的产物 |
| GET | `/api/jobs/{job_id}/files/{name}` | 单个产物文件 |
| WS | `/ws/jobs/{job_id}` | 实时事件；连接时先下发快照 |
| GET/POST | `/api/glossaries` | 列出 / 创建 |
| GET/PATCH/DELETE | `/api/glossaries/{glossary_id}` | 单个术语表 |
| GET/POST | `/api/glossaries/{glossary_id}/terms` | 列出 / 添加词条 |
| PATCH/DELETE | `/api/glossaries/{glossary_id}/terms/{term_id}` | 单个词条 |
| GET/POST | `/api/glossaries/{glossary_id}/terms.csv` | CSV 往返 |

以 `curl http://<主机>/openapi.json` 为准，上述路径即实际路径。

WebSocket 事件类型：`cues-replaced`、`cue-edited`、`cues-changed`、`review-approved`，
以及流水线原有的阶段进度事件。

## 目录结构

```
pipeline/          各阶段；每个都是可导入的纯函数
  paths.py         阶段顺序 + JobPaths（产物布局）
  runner.py        Web 工作线程的阶段调度（按名解析为可调用对象）
  download.py  transcode.py  transcribe.py
  review.py    segment.py   translate.py
  redivide.py  style.py      render.py
backend/
  app.py           FastAPI 应用、WebSocket、静态站点托管
  config.py        模型路径解析（环境变量 → pyproject → 未设置）
  db.py            engine、session、WAL 参数
  models.py        SQLModel 表结构
  jobs.py          工作线程 + 进度发布
  routers/         jobs、glossary
static/
  index.html       应用外壳
  css/app.css      样式
  js/              main、nav、sections、health
  vendor/          本地化 Tailwind
tools/check_ui.py  前端 JS 语法闸门（其余评测工具已 gitignore，见下）
```

任务产物位于 `data/jobs/<id>/`：`job-720p.mp4`、`job.wav`、`job.json`（词时间轴）、
`job.draft.txt`、`job.reviewed.txt`、`job.cues.json`、`job.ass`、`job.burned.mp4`。

### 前端校验

`tools/check_ui.py` 用真实解析器编译每个 JS 文件而不执行。UI 的语法错误是最难察觉的
一类：页面照样返回 200、样式照样渲染，只是什么都不运行。

```bash
python -m tools.check_ui
```

### 关于评测工具

`tools/` 下的评测工具（`eval_*.py`、`align_mt.py`、`ass_text.py` 等）**不进入版本库**，
已被 `.gitignore` 排除，文件仅保留在本机。原因是它们度量的是一组特定的 Ground Truth 视频、
一组特定模型与特定硬件，方法本身不具普适性——把它当作项目的一部分会误导他人。

## 法律

翻译产生的文本著作权归属原作者。默认生成的 Nixiesubs 字幕风格来自[辉光字幕组样式](https://githu.com/Kilo19/NixieVideoKit)。
本工具产出的 `.ass` 是**你的作品**，除非翻译文本源自他人的既有翻译。

所用的模型各自遵循其上游许可证。本库采用 MIT 许可。
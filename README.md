# NCAT: Nick 的计算机辅助翻译套件

当前实现是新的 `ncat` core/CLI；旧 CLI 源码从 commit `6397165` 恢复后放在本地
`legacy/`（已 gitignore，仅作行为参考，不进入发布物）。Web 端只保留一个纯 Tailwind 空壳，
模型权重、虚拟环境、后端框架和临时测试产物已移除。

> 旧中文说明：[`README_zh-cn.md`](README_zh-cn.md)

## 代码库结构

```text
ncat/                   新 core/CLI（Phase 1；media、project、ASR 协议、对齐、ASS）
tests/                  smoke/单元测试与 Full-auto golden fixture
pyproject.toml          新 ncat 包（仅 core/CLI 依赖）
GT/                     保留的 Ground Truth 测试数据（大文件，不进入 git）
static/                 纯 Tailwind WebUI 空壳
docs/                   canonical 计划、设计解释、GT 说明、旧 CLI 参考、CAT 研究
legacy/                 本地旧 CLI 源码（gitignore；仅行为参考，不发布）
```

## 旧 CLI（本地参考，不进入 git）

旧源码只在本地 `legacy/` 中，用于对照行为。

流程：

```text
转码 → 转录（.txt + .json）→ 人工翻译/校对 → Full-auto 生成 ASS → sub_renderer 压制
```

## 新 CLI

新 `ncat` 包已具备：

- `init`：创建项目目录/`project.json`；
- `ingest`：ffprobe + 720p 代理 + 16 kHz WAV，自动选择编码器；
- `transcribe`：ASR 后端协议（Whisper / whisper.cpp / stable-ts；Qwen3 预留）；
- `status`：项目状态；
- core：文本规范化、VoiceBase 转录读写、LCS/LIS/regularize 对齐、ASS 解析/写回。

```bash
python -m ncat --version
python -m ncat init my-project --source video.mp4
python -m ncat ingest my-project
python -m ncat transcribe my-project --backend whisper --model large-v3
python -m ncat status my-project
python -m unittest discover -s tests -v
```

ASR 依赖需在目标环境安装；仓库不附带虚拟环境。旧脚本只保留在本地 `legacy/`。

## WebUI 空壳

`static/index.html` 只保留 Tailwind 外壳与说明页；没有 JS 功能、没有后端、没有 API。
`static/vendor/tailwind.js` 是本地托管的 Tailwind Play CDN 构建（不进入 git），
缺失时外壳仍可显示，只是工具类无效。

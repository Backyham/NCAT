# whisper.cpp

[中文/Chinese](README_zh-cn.md)

This is the folder for compiled `whisper.cpp` executable binary and transformed `ggml` model.
Intentionally left blank.

For Mac depolyment, the folder should contain:

- `ggml-model.bin` transoformed `ggml` model from OpenAI Offcial `model-name.pt` using [`whisper.cpp scripts`](https://github.com/ggerganov/whisper.cpp/tree/master/models)
- `ggml-model-encoder.mlmodelc/` Core ML model generated using [`whisper.cpp script`](https://github.com/ggerganov/whisper.cpp/blob/master/models/generate-coreml-model.sh)
- `main` compiled `whisper.cpp` executable
- [`ggml-metal.metal`](https://github.com/ggerganov/whisper.cpp/blob/master/ggml-metal.metal) (Seems to be a Metal library)
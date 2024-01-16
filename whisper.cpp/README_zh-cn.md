# whisper.cpp

这是存放编译好`whisper.cpp`可执行文件及转换好`ggml`模型的文件夹。
故意留空。

在Mac上部署时，这个文件夹应包含以下文件/目录：

- `ggml-模型名.bin` 使用[`whisper.cpp脚本`](https://github.com/ggerganov/whisper.cpp/tree/master/models)、由OpenAI官方`模型名.pt`转换而来的 `ggml`模型 
- `ggml-模型名-encoder.mlmodelc/` 使用[`whisper.cpp脚本`](https://github.com/ggerganov/whisper.cpp/blob/master/models/generate-coreml-model.sh)生成的Core ML模型
- `main` 编译好的`whisper.cpp`可执行文件
- [`ggml-metal.metal`](https://github.com/ggerganov/whisper.cpp/blob/master/ggml-metal.metal) (好像是个Metal库)

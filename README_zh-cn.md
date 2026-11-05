# NCAT: Nick的计算机辅助翻译（CAT）脚本套件

这个Repo是用以实现Nick定制视频翻译工作流的CAT方案。

该方案源自辉光字幕组工作流，借用了部分辉光字幕组的代码和脚本。

这权当是我自己的消遣试验场，同时用来测试与学习自动化与编程，菜请多包容。

因此顺便, __感谢 [dummyx](https://github.com/dummyx) 对我无穷无尽弱智问题的解答，以及大量原始工具的作者 [Kilo19](https://github.com/Kilo19)。__

## 结构

或者说，我翻译视频的流程。

我的工作流依照辉光字幕组习惯，所以辉光基本上也是这么翻译LMG视频的。

(EX ~~这种关卡还需要解释吗~~ 代表超出我能力的重量级任务)

- 第一步：拿到视频.  -> `NULL`

  >辉光字幕组使用LMG自有订阅视频平台Floatplane.com的无广告版视频。

  （Youtube）视频下载自动化(尚未实现)
- 第二步：  -> `pre_transcoder.py`
  
  >辉光字幕组原先使用`Voicebase`转写服务，它的单词时间戳精度尚可，可惜听写文本质量不甚理想。后来换用AWS的服务，结果在偏技术的内容上表现反而更差了。目前组里使用原版[`OpenAI Whisper ASR`](https://github.com/openai/whisper)，它的听写表现出色，但时间戳精度很糟糕。

  - A：从视频转录出用于翻译的文本。 -> `transcribe.py`

      在一张英伟达~~GTX 1080 Ti 24GB~~ P40上部署了[`Whisper`](https://github.com/openai/whisper)。目前暂时使用部署在Mac上、使用`Core ML`的[`whisper.cpp`](https://github.com/ggerganov/whisper.cpp)。

    - EX I: 做一个翻译工作区出来。
  
      >辉光字幕组没有使用任何CAT软件方案。

      但我对此颇有兴趣，可能需要参考一些其他的CAT软件，或许会使用[`Aegisub`](https://github.com/TypesettingTools/Aegisub)改造以整合打时间轴的工作。预计会是个长期项目。

    - EX II: [DeepL](https://www.deepl.com/translator)是个挺好用的机器翻译。

      有兴趣深究，但有组员反馈过它的API输出质量很一般，和网页版天差地别。

  - B: 从视频转录出用于生成时间轴的json文件。 -> `transcribe.py`

    >敏感的各位可能注意到这个顺序了。没错，辉光字幕组和其他一些字幕组的工作流程相反：先翻译、后校对、再打轴、最后渲染。

    >辉光字幕组原先使用`Voicebase`转写服务

    这就是为什么有一部分代码是要把`Whisper`的`json`转换成`VoiceBase`的`json`。

    >目前组里使用原版[`OpenAI Whisper ASR`](https://github.com/openai/whisper)，它的听写表现出色，__但时间戳精度很糟糕__。

    单词时间戳精度对打轴 __极度重要__。所以我有几个想法：

    - a. 研究一下`Whisper`增强件[`stable-ts`](https://github.com/jianfch/stable-ts)；（还没试）

    - b. `Whisper`的其他实现版本[`whisperX`](https://github.com/m-bain/whisperX)；(`Anaconda`在我这里有问题，可能以后会绕开它再尝试)

    - c. `Whisper`的其他实现版本[`whisper.cpp`](https://github.com/ggerganov/whisper.cpp)（还挺成功）

  - EX III: `whisper.cpp`提到`Whisper ASR`[可以加载定制精调非OpenAI官方的模型](https://github.com/ggerganov/whisper.cpp/blob/master/models/README.md#fine-tuned-models)。有趣有趣。

- 第三步: 自动ASS字幕文件生成。  -> `Full-auto_M134A16.py`
  >这是辉光组[Kilo19](https://github.com/Kilo19)的原创脚本。
  
  计划：

  - a. 把字幕样式换成自己的；
  - b. 理解脚本原理以后自己写一遍。

- 第四步: 将字幕渲染上视频。 -> `ass_encoder.py`

  借用辉光字幕组脚本算是实现了，换用[更纱黑体](https://github.com/be5invis/Sarasa-Gothic)，使用修改后的[辉光样式](https://github.com/Kilo19/NixieVideoKit)（特别感谢[Kilo19](https://github.com/Kilo19)的原始样式）。由FFmpeg驱动。

## 配置whisper.cpp

按照`whisper.cpp` README操作即可。有一点迷惑了我半天的点是，在Mac上部署时，要先走完`Quick Start`然后再操作`Core ML support`。两者都是必要的。

## 授权啥的

MIT授权, 随意使用。

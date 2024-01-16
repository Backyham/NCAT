# NCAT: Nick's Computer-Aided Translation Script Suite

[中文/Chinese](README_zh-cn.md)

This is a CAT solution to implement Nick's tailored video translation workflow.

Derived from Nixiesubs' workflow, using some Nixiesub codes and scripts.

Please consider this repo as a playground for me to play, try, test and learn automation and programming stuff, so forgive how entry-level this might appear.

Speaking of which, __thank [dummyx](https://github.com/dummyx) for answering my countless newbie questions, and [Kilo19](https://github.com/Kilo19) for a lot of origin tools.__

## Structrue

In other words, how do I translate videos?

My workflow follows Nixiesub's traditions, so basically this is how Nixiesub translate a LMG video.

(EX indicates a heavy task that seems to be out of my capabilities.)

- Step I: Get video.  -> `NULL`

  >Nixiesub uses Ad-Free version videos from LMG's own content subcription platform Floatplane.com.

  (Youtube) Video Automated Download (not implemented)
- Step II:  -> `pre_transocoder.py`
  
  >Nixiesub used to use a trancribe service `Voicebase`. While it provided decent word-level timestamps, the transcription was less than desirable. Later we moved to AWS' service and got worse output on technical stuff. Currently we're using vanilla [`OpenAI Whisper ASR`](https://github.com/openai/whisper) and found it has exellent performance on transciption but poor timestamp accuracy.

  - A: Transcribe video to text for translation.  -> `transcribe.py`

      Deployed a [`Whisper`](https://github.com/openai/whisper) on a Nvidia ~~GTX 1080 Ti 24GB~~ P40. Currenty migrated to [`whisper.cpp`](https://github.com/ggerganov/whisper.cpp) on Mac using `Core ML` temporarily.

    - EX I: Implement a translataion workspace.
  
      >Nixiesub do not use any Computer Aided Translation solution.

      But I'm interested. Need to check out more CAT softwares, may base on [`Aegisub`](https://github.com/TypesettingTools/Aegisub) to incorporate timeline jobs. Expected to be a long-term project.

    - EX II: [DeepL](https://www.deepl.com/translator) is a quite handy machine translator.

      Interested to dive in although some team member reported the API output quality is far cry for webUI.

  - B: Transcribe video to json for timeline.  -> `transcribe.py`

    >Keen-eyed might notice this order: Yes, Nixiesubs do things in a reversed order to some other subtite groups: translate first, then proofread, then figure out timeline, finally render.

    >Nixiesub used to use a trancribe service `Voicebase`.

    That's why the code include a part that transform `Whisper json` to `VoiceBase json`.

    >Currently we're using vanilla [`OpenAI Whisper ASR`](https://github.com/openai/whisper) and found it has _exellent performance on transciption but __poor timestamp accuracy__.

    Word-level timestamp accuracy is __critical__ for timeline work. Therefore, I have several ideas:

    - a. Explore `Whisper` enhancement [`stable-ts`](https://github.com/jianfch/stable-ts); (Haven't tried)

    - b. Alternate `Whisper` implementation [`whisperX`](https://github.com/m-bain/whisperX); (`Anaconda` causing me problems, might try without it)

    - c. Alternate `Whisper` implementation [`whisper.cpp`](https://github.com/ggerganov/whisper.cpp) (Quite succesful)

  - EX III: `whisper.cpp`  mentioned `Whisper ASR` [can load custom fine-tuned not-OpenAI-Official model](https://github.com/ggerganov/whisper.cpp/blob/master/models/README.md#fine-tuned-models). Interesting.

- Step III: Automated ASS Subtitle Generation.  -> `Full-auto_M134A16.py`
  >This is [Kilo19](https://github.com/Kilo19) of Nixiesub's original creation.
  
  Intended to:

  - a. Replace the styles to be my own;
  - b. When fully understand the mechanics, rebuild myself.

- Step IV: Render video out with subtitles.  -> `ass_encoder.py`

  Completed by copy NixieSubs' script, using modified font ([Sarasa Gothic](https://github.com/be5invis/Sarasa-Gothic)) and style([Origin](https://github.com/Kilo19/NixieVideoKit), special thank to [Kilo19](https://github.com/Kilo19) for original styles). Powered by FFmpeg.

## whisper.cpp Setup

Follow `whisper.cpp` README. One thing to note (that confused me for a while), if to deploy on Macs, do section `Quick Start` then do `Core ML support`, none of the both can be skipped.

## Legal Stuff

MIT License, use it as you wish.

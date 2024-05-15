#!/usr/bin/env python3

import wave
import sys

from vosk import Model, KaldiRecognizer, SetLogLevel

# You can set log level to -1 to disable debug messages
SetLogLevel(0)

input_wave = input("WAV, please:\n")
wf = wave.open(input_wave, "rb")
    
if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != "NONE":
    print("Audio file must be WAV format mono PCM.")
    sys.exit(1)

model = Model("vosk-model-en-us-0.42-gigaspeech")

# You can also init model by name or with a folder path
# model = Model(model_name="vosk-model-en-us-0.21")
# model = Model("models/en")

rec = KaldiRecognizer(model, wf.getframerate())
rec.SetWords(True)
rec.SetPartialWords(True)

result_json = {}
with open("result.json", "w") as f:
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            pass
            # f.write(str(rec.Result()))
            # result_json.update(rec.Result())
        
    f.write(str(rec.FinalResult()))
        
#    json.dump(result_json, f)
        
'''
Vosk proc
'''
import wave
import vosk

from vosk_trans import Pre_transcode

def vosk_init(WAV_input:str):
    model = vosk.Model(model_name="vosk-model-en-us-0.42-gigaspeech")
    worker = vosk.KaldiRecognizer(model, 16000)
    worker.SetWords(True)
    worker.SetPartialWords(True)
    return worker

def vosk_trans():
    pass
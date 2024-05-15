'''
Reconstructed original version.
New version pending, to be released with new ASS generator.
-------
Transcribe video/audio to text file and VoiceBase-style json.
Powered by OpenAI Whisper Automatic Speech Recognition.
Special thanks to @dummyx for answering my newbie questions and his code.

A part of code in here is to transform Whisper JSON output to VoiceBase-Style JSON.
There's a historical reason behind this 
since this workflow is derived from Nixiesub, using Nixiesub tools.
And I, as of now, might not have the abillity to avoid this workaround.
Not before Full-auto_M134A16.py reconstruction.
'''
import json, os, sys

from transcode import transcode_func as tc
from nltk_func import nltk_proc as np

from exception_proc import show_exception_and_exit
sys.excepthook = show_exception_and_exit

avail_asr = {"1":"Whisper ASR (OpenAI Vanilla Vesion)",
             "2":"whisper.cpp",
             "3":"stable-ts Enhanced Whisper",
             "4":"Alphacei Vosk",
             "5":"Whisper/Vosk Mixture"}

def whisper_vanilla(audio:str):
    import whisper
    print("\nYou've selected Vanilla OpenAI Whisper ASR.\nProcessing with large-v3 model.\n")
    
    model = whisper.load_model('large-v3')
    tran_result = model.transcribe(audio, 
                                   word_timestamps = True, 
                                   initial_prompt = '', 
                                   prepend_punctuations = '', 
                                   append_punctuations = '')
    vbjson_dict = {"transcript":{"words":[]}}
    position = 0
    prev = 0
    for word_list in tran_result['segments']:
        for word in word_list['words']:
            vb_start = int(word['start'] * 1000 + 200)
            vb_end   = int(word['end']   * 1000)
            vb_text  = word['word'].replace(" ","").replace(".","")  # Thanks dummyx, again!
            
            if vb_text == "" and position != 0:
                vbjson_dict['transcript']['words'][position-1]['e'] = vb_end
                prev = vb_end
                continue
            if vb_start < prev: vb_start = prev
            
            vbjson_dict['transcript']['words'].append({"p": position, "s": vb_start, "e": vb_end, "w": vb_text})
            prev = vb_end
            position += 1
            
    return (vbjson_dict, tran_result['text'])

def whipser_cpp(audio:str):
    import pathlib
    import subprocess as sp
    from os.path import join as osjoin    
    print("\nYou've selected whisper.cpp.\nProcessing with large-v3 model.\n")
    
    directory = pathlib.Path(__file__).parent.resolve()
    exec_path  = osjoin(directory, 'whisper.cpp/main')
    model_path = osjoin(directory, 'whisper.cpp/ggml-large-v3-q5_0.bin')
    
    trancribe_args = [str(exec_path),
                      "-m", str(model_path),
                      "-l", "en",
                      "-ojf",
                      "-f", audio]
    sp.run(trancribe_args, capture_output=True)
    
    with open(audio + '.json', 'r') as f:
        result_json = json.load(f)
    
    vbjson_dict = {"transcript":{"words":[]}}
    position = 0
    prev = 0
    text_temp = ''
    for segment in result_json['transcription']:
        text_temp += segment['text']
        prev = segment['offsets']['from']
        for token in segment['tokens']:
            vb_start = token['offsets']['from'] + 400
            vb_end   = token['offsets']['to'] + 300
            vb_text  = token['text'].replace(" ","").replace(".","")

            if vb_text == "" and position != 0:
                vbjson_dict['transcript']['words'][position - 1]['e'] = vb_end
                prev = vb_end
                continue
                
            if vb_start < prev: vb_start = prev

            vbjson_dict['transcript']['words'].append({"p": position, "s": vb_start, "e": vb_end, "w": vb_text})
            prev = vb_end
            position += 1
            
    os.remove(audio + '.json')
    return (vbjson_dict, text_temp)

def whisper_ts(audio:str):
    import stable_whisper
    print("\nYou've selected stable-ts Enhanced Whisper ASR.\nProcessing with large-v3 model.\n")
    
    model = stable_whisper.load_model('large-v3')
    crude_result = model.transcribe(audio, 
                                    verbose = False,
                                    word_timestamps = True,
                                    language = 'en',
                                    vad = True)
    result = model.refine(audio, crude_result, precision = 0.02)
    result.save_as_json(audio + '.json')
        
    with open(audio + '.json', 'r') as f:
        result_json = json.load(f)
        
    text_temp = ''
    vbjson_dict = {"transcript":{"words":[]}}
    position = 0
    prev = 0
    for segment in result_json['segments']:
        text_temp += segment['text']
        prev = segment['start']
        for word in segment['words']:
            vb_start = int(word['start'] * 1000 + 50)
            vb_end   = int(word['end'] * 1000 + 200)
            vb_text  = word['word'].replace(" ","").replace(".","")

            if vb_text == "" and position != 0:
                vbjson_dict['transcript']['words'][position - 1]['e'] += 100
                prev = vb_end
                continue
            if vb_start < prev: vb_start = prev

            vbjson_dict['transcript']['words'].append({"p": position, "s": vb_start, "e": vb_end, "w": vb_text})
            prev = vb_end
            position += 1
            
    os.remove(audio + '.json')
    return (vbjson_dict, text_temp)
    
def transcribe_func(asr_sel:int, audio_arg_list:list): 
    if asr_sel in {4,5}:
        raise NotImplementedError("\nASR method did not implemented yet. Sorry.")
    
    tc(audio_arg_list)
    
    if asr_sel == 1:
        result_json, full_text = whisper_vanilla(audio_arg_list[-1])
    elif asr_sel == 2:
        result_json, full_text = whipser_cpp(audio_arg_list[-1])
    elif asr_sel == 3:
        result_json, full_text = whisper_ts(audio_arg_list[-1])
    
    with open(audio_arg_list[-1].split('.')[0] + '.txt', 'w') as f:
        f.write(np(full_text))
    with open(audio_arg_list[-1].split('.')[0] + '.json', 'w') as f:
        f.write(json.dumps(result_json))
    os.remove(audio_arg_list[-1])
    
    return 0

if __name__ == "__main__":
    pass
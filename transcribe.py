'''
Transcribe video/audio to text file and VoiceBase-style json.
Powered by OpenAI Whisper Automatic Speech Recognition.
Special thanks to @dummyx for answering my newbie questions and his code.

A part of code in here is to transform Whisper JSON output to VoiceBase-Style JSON.
There's a historical reason behind this 
since this workflow is derived from Nixiesub, using Nixiesub tools.
And I, as of now, might not have the abillity to avoid this workaround.
Not before Full-auto_M134A16.py reconstruction.
'''
# Modules
import json
import pathlib
import whisper
import subprocess
from os.path import join as osjoin
from nltk import tokenize, download

vbjson_dict = {"transcript":{"words":[]}}
text = ''

def nltk_proc(text_result:str):
    try:
        sentences = tokenize.sent_tokenize(text_result)
    except LookupError:
        download("punkt")
        sentences = tokenize.sent_tokenize(text_result)
    processed_text = '\n'.join(sentences)
    return processed_text

#Whisper transcribe
def whisper2voicebase(extracted_audio:str, model_sel:int):
    if model_sel == 1:
        print("\nYou've selected Vanilla OpenAI Whisper ASR.\nProcessing with large-v3 model.\n")
        model = whisper.load_model('large-v3')
        tran_result = model.transcribe(extracted_audio, 
                                       word_timestamps = True, 
                                       initial_prompt = '', 
                                       prepend_punctuations = '', 
                                       append_punctuations = '')
        text = nltk_proc(tran_result['text'])
        
        position = 0
        prev = 0
        for word_list in tran_result['segments']:
            for word in word_list['words']:
                vb_start = int(word['start'] * 1000 - 300)
                vb_end   = int(word['end'] * 1000)
                vb_text  = word['word'].replace(" ","").replace(".","")
                # Thanks dummyx, again!
                if vb_text == "" and position != 0:
                    vbjson_dict['transcript']['words'][position-1]['e'] = vb_end
                    prev = vb_end
                    continue
                
                if vb_start < prev: vb_start = prev
            
                vbjson_dict['transcript']['words'].append(
                    {"p": position, "s": vb_start, "e": vb_end, "w": vb_text})
                prev = vb_end
                position += 1

    if model_sel == 2:
        print("\nYou've selected whisper.cpp.\nProcessing with large-v3 model.\n")
        directory = pathlib.Path(__file__).parent.resolve()
        exec_path  = osjoin(directory, 'whisper.cpp/main')
        model_path = osjoin(directory, 'whisper.cpp/ggml-large-v3-q5_0.bin')
        trancribe_args = [str(exec_path),
                         "-m", str(model_path),
                         "-l", "en",
                         "-ojf",
                         "-f", extracted_audio]
        subprocess.run(trancribe_args, capture_output=True)
        with open(extracted_audio + '.json', 'r') as f:
            result_json = json.load(f)
            
            text_temp = ''
            position = 0
            prev = 0
            for segment in result_json['transcription']:
                text_temp += segment['text']
                prev = segment['offsets']['from']
                for token in segment['tokens']:
                    vb_start = token['offsets']['from'] - 300
                    vb_end   = token['offsets']['to']
                    vb_text  = token['text'].replace(" ","").replace(".","")
                    
                    if vb_text == "" and position != 0:
                        vbjson_dict['transcript']['words'][position-1]['e'] = vb_end
                        prev = vb_end
                        continue
                    
                    if vb_start < prev: vb_start = prev
                    
                    vbjson_dict['transcript']['words'].append(
                        {"p": position, "s": vb_start, "e": vb_end, "w": vb_text})
                    prev = vb_end
                    position += 1
                    
            text = nltk_proc(text_temp)
                        
    with open(extracted_audio.split('.')[0] + '.txt', 'w') as f:
        f.write(text)
    with open(extracted_audio.split('.')[0] + '.json', 'w') as f:
        f.write(json.dumps(vbjson_dict))
    return 0
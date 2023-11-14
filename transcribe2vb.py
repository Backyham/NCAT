'''
Transcribe video/audio to text file and VoiceBase-style json.
Powered by OpenAI Whisper Automatic Speech Recognition.
Special thanks to @dummyx for answering my newbie questions and his code.

A part of code in here is to transform Whisper JSON output to VoiceBase-Style JSON.
There's a historical reason behind this 
since this workflow is derived from Nixiesub, using Nixiesub tools.
And I, as of now, might not have the abillity to avoid this workaround.
Not before other tools have been reconstructed.
'''
import json
import whisper
from nltk import tokenize, download

#Whisper transcribe
audio_file_dir = input("Audio file, Please.\n")

print("\nProcesseing with large-v3 model.\n")
model = whisper.load_model('large-v3')
trans_result = model.transcribe(audio_file_dir, language = 'en', word_timestamps = True, initial_prompt = None, verbose = True)

# Copied directly from dummyx. Understand the use of it, not quite understand the mechanic.
try:
    sentences = tokenize.sent_tokenize(trans_result['text'])
except LookupError:
    download("punkt")
    sentences = tokenize.sent_tokenize(trans_result['text'])
text = '\n'.join(sentences)

#Whisper json to txt
with open(audio_file_dir.split('.')[0] + '.txt', 'w') as f:
    f.write(text)

#Whisper json to VB json
vbjson_dict = {"transcript":{"words":[]}}
position = 0

for word_list in trans_result['segments']:
    prev = word_list['start']
    for word in word_list['words']:
        vb_start = word['start'] * 1000
        vb_end = word['end'] * 1000
        #Thanks dummyx, again!
        vb_text = word['word'].replace(" ","").replace(".","")

        if position > 0:
            vb_start -= 400
            vb_end += 100
            if vb_start < prev: vb_start = prev +1
            if vb_start < 0: vb_start = 0

        vbjson_dict['transcript']['words'].append(
            {"p": position, "s": vb_start, "e": vb_end, "w": vb_text})
        prev = vb_end
        position += 1

result_json = json.dumps(vbjson_dict)
with open(audio_file_dir.split('.')[0] + '.json', 'w') as f:
    f.write(result_json)

print("Finished.\n")
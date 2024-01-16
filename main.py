# Library
import os
import subprocess
# Script
from pre_transcoder import transcode_for_whisper as pre_tc
from transcribe     import whisper2voicebase     as wh2vb

if __name__ == "__main__":
    whisper_select = int(input("\nPlease select which version you'd like to use:\n1. Whisper (vanilla)\n2. whisper.cpp\n\nType the number: "))
    og_video = input("\nPlease put in the video file to transcribe: \n")
    tc_result = pre_tc(og_video)
    wh2vb(tc_result, whisper_select)
    print("Finished.")
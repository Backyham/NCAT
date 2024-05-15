'''
Transcoder powered by ffmpeg. Highly probable this won't work in Windows.

This script takes a video file and output two files:
a) A lower resolution (1080 pixel on short side, maintain aspect ratio) proxy video for timeline;
b) An PCM WAV audio file for Whisper.

Return WAV file name.
'''
import subprocess
import sys
import pathlib

def Pre_transcode(video_input: str):
    video_path = pathlib.Path(video_input)
    if video_input.startswith('~'):
        video_path = video_path.expanduser()
    video_path = str(video_path)
    
    if sys.platform == 'darwin':   # Mac
        hw_acc = 'h264_videotoolbox'
        hw_au_acc = 'aac_at'
        print("\nmacOS detected. Trying Apple VideoToolBox/AudioToolBox...\n")
    else:
        hw_acc = 'h264_nvenc'
        hw_au_acc = 'aac'
        print("Not Mac. Assume Nvidia NVENC/AAC...")
    
    video_tc_args = ["ffmpeg",
                     "-i",   video_path,
                     "-c:v", hw_acc,
                     "-b:v", "1M",
                     "-c:a", hw_au_acc,
                     "-b:a", "128K",
                     "-vf",  "scale=-1:720",
                     "-y",
                     video_path.split(".")[0] + '-720p.mp4']

    audio_tc_args = ["ffmpeg",
                     "-i", video_path,
                     "-vn",
#                    whisper.cpp only works with 16-bit 16KHz PCM WAV file.
                     "-c:a", "pcm_s16le",
                     "-ac", "1",
                     "-ar", "16000",
                     "-y",
                     video_path.split(".")[0] + '.wav']

    print("Encoding audio...")
    subprocess.run(audio_tc_args, capture_output=True)
    
    print("Encoding video, trying Apple Media HW Encoder...")
    sp = subprocess.run(video_tc_args, capture_output=True)
    if sp.returncode == 254:  #FFmpeg exit code 254 - File not found
        raise FileNotFoundError("\nInput video not found. Please check if you put in the correct file path and name.\n未找到输入文件。请检查文件目录和文件名输入是否正确。")
    if sp.returncode == 8:    #FFmpeg exit code 8   - Encoder not found (Unspported)
        hw_acc = 'h264_qsv'
        print("Failed. Trying Intel QuickSync...")
        sp = subprocess.run(video_tc_args, capture_output=True)
        if sp.returncode == 8:
            hw_acc = 'h264_amf'
            print("Failed. Trying AMD Media Encoder...")
            sp = subprocess.run(video_tc_args, capture_output=True)
            if sp.returncode == 8:
                hw_acc = 'libx264'
                print("Failed. Failing-over to CPU encoding...")
                sp = subprocess.run(video_tc_args, capture_output=True)
                if sp.returncode != 0:
                    raise SystemError("\nUnknown Error, return exit code", sp.returncode, ".\n未知错误，FFmpeg返回值", sp.returncode, "。")
    
    return video_path.split(".")[0] + '.wav'
'''
Transcoder powered by ffmpeg.

This script takes a video file and output two files:
a) A lower resolution (1080 pixel on short side, maintain aspect ratio) proxy video for timeline;
b) An AAC audio file for Whisper.
'''
import subprocess
input_video = input("Video file, please.\n")


video_tc_args = ["ffmpeg",
                 "-i", input_video,
                 "-c:v", "h264_nvenc",
                 "-b:v", "6M",
                 "-c:a", "aac",
                 "-b:a", "256K",
                 "-vf", "scale=-1:1080",
                 input_video.split(".")[0] + '.mp4']

audio_tc_args = ["ffmpeg",
                 "-i", input_video,
                 "-vn",
                 "-c:a", "aac",
                 "-b:a", "320K",
                 input_video.split(".")[0] + '.aac']

subprocess.run(audio_tc_args)
subprocess.run(video_tc_args)
print("\nCompeleted.\n")
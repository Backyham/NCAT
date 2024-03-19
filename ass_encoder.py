'''
Raw meat cooker. By Nick Lian.
Sorry.

ASS subtitle renderer. Copied from Nixiesub Script. Special thanks.
'''
from fractions import Fraction
import os
import shutil
import subprocess

video_file = input("Original video, please.\n")
sub_file = input("ASS file, please.\n")
output_file = '[BE4K6M]' + video_file.split('.')[0] + '_NickLian.mp4'

# Get framerate fot key frame distance - Seems incompatible with webm format.
# Scratch that. I'm a idiot. 

# ffprobe
# if video_file.split('.')[1] == 'mp4':
#    ffprobe_arg = ['ffprobe',
#                    '-v', 'error',
#                    '-select_streams', 'v',
#                    '-of', 'default=noprint_wrappers=1:nokey=1',
#                    '-show_entries', 'stream=r_frame_rate',
#                    video_file]
#    ffprobe_run = subprocess.run(ffprobe_arg, capture_output=True)
#    key_int = int(float(Fraction(ffprobe_run.stdout.decode())) * 10)

sub_temp = "b4ke_subtmp.ass"
shutil.copyfile(sub_file, os.path.join(os.path.dirname(sub_file), sub_temp))

ffmpeg_arg = ["ffmpeg",
              "-i", video_file,
              "-c:a", "aac",
              "-b:a", "320K",
#             "-c:v", "h264_nvenc",
              "-c:v", "libx264",
              "-b:v", "20M",
              "-preset", "slow",
              "-crf", "20",
              "-minrate", "18M",
              "-maxrate", "25M",
              "-bufsize", "50M",
#             "-x264-params", "vbv-maxrate=10000:vbv-bufsize=40000",
#             Even with Intel Core i7-13600K's six P-Cores, libx264 is to slow. 
#             Not recommending for web publishment.
              "-filter_complex", 'scale=3840:-1:flags=lanczos[sc];[sc]ass=f=\'%s\'' % os.path.join(os.path.dirname(sub_file), sub_temp),
              output_file]

subprocess.run(ffmpeg_arg)
os.remove(sub_temp)

'''
Transcode Module of NCAT Toolkit.
Transcode input video into:
a) a 720p 1Mbps H.264 proxy video for timeline work;
b) a 16-bit 16KHz Mono PCM WAV audio for ASR.
'''
import multiprocessing as mp
import subprocess      as sp
import sys, pathlib, os.path

from exception_proc import show_exception_and_exit
sys.excepthook = show_exception_and_exit
    
def input_cleanse(path_input: str):
    path = pathlib.Path(path_input)
    if path_input.startswith('~'):
        path = path.expanduser()
        
    if os.path.exists(path):
        return str(path)
    else:
        raise FileNotFoundError("\nFile not found.\n未找到文件。")

def transcode_args(video: str):
    hw_a_enc, hw_v_enc = ('' , '')
    non_mac_codex = ['h264_nvenc', 'h264_qsv', 'h264_amf', 'libx264']
    windows = {'win32','cygwin'}
    null_target = ''
    if sys.platform in windows:
        null_target = 'NUL'
    else:
        null_target = '/dev/null'
    
    if sys.platform == 'darwin':   # Mac
        hw_v_enc = 'h264_videotoolbox'
        hw_a_enc = 'aac_at'
    else:
        hw_a_enc = 'aac'
        for codec in non_mac_codex:
            codec_test = sp.run(["ffmpeg",
                                 "-y",
                                 "-f",  "lavfi",
                                 "-i",  "nullsrc=s=hd720",
                                 "-t",  "1",
                                 "-c:v", codec,
                                 "-f",  "mp4",
                                 null_target], capture_output=True)
            if codec_test.returncode != 8:
                hw_v_enc = codec
                break        
        
    video_tc_args = ["ffmpeg",
                     "-i",   video,
                     "-c:v", hw_v_enc,
                     "-b:v", "1M",
                     "-c:a", hw_a_enc,
                     "-b:a", "128K",
                     "-vf",  "scale=-1:720",
                     "-y",
                     video.split(".")[0] + '-720p.mp4']
    audio_tc_args = ["ffmpeg",
                     "-i", video,
                     "-vn",
                     "-c:a", "pcm_s16le",
                     "-ac", "1",
                     "-ar", "16000",
                     "-y",
                     video.split(".")[0] + '.wav']
    
    return (video_tc_args, audio_tc_args)

def transcode_func(func_arg: list, subpreocess_caputure_output=True):
    if not '-vn' in func_arg:
        print('Encoding video proxy. Target format: 1Mbps 720p H.264 & 128Kbps AAC')
    else:
        print('Encoding audio. Target format: 16-bit 16KHz Mono PCM WAV.')
    
    sp_instance = sp.run(func_arg, capture_output=subpreocess_caputure_output)
   
    if not '-vn' in func_arg:
        print('\nVideo transcode compelete.\nPath:', func_arg[-1], '\n')
    else:
        print('\nAudio transcode compelete.\nPath:', func_arg[-1], '\n')
        
    return sp_instance.returncode
    
if __name__ == "__main__":
    video_path = input_cleanse(input("Video here, please:\n"))
    video_args, audio_args = transcode_args(video_path)
    arg_list = [video_args, audio_args]
    
    duo = mp.Pool(processes=2)
    ps_list = []
    
    for arg in arg_list:
        ps = duo.apply_async(transcode_func, (arg, ))
        ps_list.append(ps)
    
    duo.close()
    duo.join()
    
    for ps in ps_list:
        if ps.get() != 0:
            raise SystemError("Unknown Error, return exit code", ps.get(), ".\n未知错误，返回值", ps.get(), "。")
    
    print("Trancode compelete.\n\n")
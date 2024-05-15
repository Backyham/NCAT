import os, sys

import multiprocessing as mp

import transcode
from transcribe import transcribe_func, avail_asr

from exception_proc import show_exception_and_exit
sys.excepthook = show_exception_and_exit

if __name__ == "__main__":
    print("\nPlease select which version you'd like to use:")
    for index in avail_asr:
        print(index+'.', avail_asr[index])
    while True: 
        asr_select = int(input("Type the number: "))
        if asr_select in set(avail_asr.keys()):
            break
        else:
            print("Invalid selection. Try again.")
    
    print("\nPlease put in the video file to transcribe, finish with a empty line: \n")
    video_path_list = []
    while True:
        name = input()
        if name == '':
            break
        else:
            try:
                video_path_list.append(transcode.input_cleanse(name))
            except FileNotFoundError:
                print("File path do not exist. Check input and retry, please.\n")
                continue
    
    video_transcode_args_list = []
    audio_transcode_args_list = []
    if video_path_list == []:
        print("Nothing provided. Exit.")
    else:
        for path in video_path_list:
            video_arg, audio_arg = transcode.transcode_args(path)
            video_transcode_args_list.append(video_arg)
            audio_transcode_args_list.append(audio_arg)
    
    transcode_pool  = mp.Pool(min(8, os.cpu_count(), len(video_transcode_args_list)))
    transcribe_pool = mp.Pool(min(4, os.cpu_count(), len(audio_transcode_args_list)))
    ps_list = []
    
    for v_arg in video_transcode_args_list:
        v_tc_ps = transcode_pool.apply_async(transcode.transcode_func, (v_arg, ))
        ps_list.append(v_tc_ps)
    transcode_pool.close()
    
    for a_arg in audio_transcode_args_list:
        a_tc_ps = transcribe_pool.apply_async(transcribe_func, (asr_select, a_arg))
        ps_list.append(a_tc_ps)
    transcribe_pool.close()
    
    transcode_pool.join()
    transcribe_pool.join()
    
    for ps in ps_list:
        if ps.get() != 0:
            raise SystemError("Unknown Error, return exit code", ps.get(), ".\n未知错误，返回值", ps.get(), "。")
    
    print("Finished.")
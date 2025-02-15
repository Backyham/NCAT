import os, sys, time

import transcode
from transcribe import transcribe_func, avail_asr
from file_in import input_cleanse

from exception_proc import show_exception_and_exit
sys.excepthook = show_exception_and_exit

if __name__ == "__main__":
    print("\nPlease select which you'd like to use:")
    for index in avail_asr:
        print(index+'.', avail_asr[index][0])
    while True: 
        asr_select = int(input("Type the number: "))
        if str(asr_select) in set(avail_asr.keys()):
            if str(asr_select) in {4,5}:
                print("ASR method did not implemented yet. Sorry and please choose another.\n该听写方式尚未实现，抱歉。请选择其他方式。")
                continue
            else: 
                break
        else:
            print("Invalid selection. Try again.")
    
    video_path_list = []
    while True:
    #CLI UI:
        print("\nPlease put in the video file to transcribe, EOF/Ctrl-D to finish: ")
    #for i in file_list:
    #   print i + "\n"
    #
        try:
            name = input()
            if input_cleanse(name) != None:
                video_path_list.append(input_cleanse(name))
        except EOFError:
            break
    
    transcode_args_list = []
    if video_path_list == []:
        print("Nothing provided. Exit.")
        sys.exit(-1)
    else:
        for path in video_path_list:
            video_arg, audio_arg = transcode.transcode_args(path)
            transcode_args_list.append(video_arg)
            transcode_args_list.append(audio_arg)
    
    print("\nStep 1 - Transcode - Start.\n")
    
    transcribe_traget = []
    for arg in transcode_args_list:
        try:
            exitcode = transcode.transcode_func(arg)
            if exitcode != 0:
                raise SystemError(arg, exitcode)
        except SystemError as error_inst:
            if not '-vn' in error_inst.args[0]:
                print('Video proxy transcode for task "' + error_inst.args[0][2] + '" failed. FFmpeg returned', error_inst.args[1], '. Video proxy may be not rendered.')
                print('视频 "' + error_inst.args[0][2] + '" 的代理转码失败，FFmpeg返回值', error_inst.args[1], '，代理可能未能生成。\n')
            else:
                print('Audio transcode for task "' + error_inst.args[0][2] + '" failed. FFmpeg returned', error_inst.args[1], '. Audio may be not generated.')
                print('视频 "' + error_inst.args[0][2] + '" 的音频转码失败，FFmpeg返回值', error_inst.args[1], '，音频文件可能未能生成。\n')
        else:
            if '-vn' in arg:
                transcribe_traget.append(arg[-1])
                
    print("\nStep 1 finished.\nStep 2 - Transcribe - Start.\n")
    
    for i in transcribe_traget:
        transcribe_func(asr_select, i)
    
    print("Finished.")
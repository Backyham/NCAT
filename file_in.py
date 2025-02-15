import pathlib

def input_cleanse(path_input: str):
    path = pathlib.Path(path_input)
    if path_input.startswith('~'):
        path = path.expanduser()
    
    if path.is_file():
        return str(path)
    else:
        print("File not found. Please check file path/file name.\n文件不存在，请检查您输入的文件路径/文件名。")
        input("\nAny key to continue…\n按任意键继续……")
        return None
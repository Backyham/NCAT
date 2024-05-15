import sys
def show_exception_and_exit(exc_type, exc_value, tb):
	import traceback
	traceback.print_exception(exc_type, exc_value, tb)
	input("脚本遇到错误，按回车键退出。\nError encountered, Press Enter to Exit.")
	sys.exit(-1)
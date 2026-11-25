"""程序入口：开发时直接双击，打包时作为 PyInstaller 的入口脚本。"""
import sys

from touping.app import main

if __name__ == "__main__":
    sys.exit(main())

"""假的 scrcpy：第一个参数决定行为。"""
import sys
import time

mode = sys.argv[1]
if mode == "fail":
    print("ERROR: Could not find any ADB device", flush=True)
    sys.exit(1)
if mode == "disconnect":
    sys.exit(2)
if mode == "hang":          # 没有窗口、不理会 WM_CLOSE
    time.sleep(60)
if mode == "window":
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    w = QWidget()
    w.setWindowTitle("fake")
    w.setGeometry(200, 200, 420, 320)
    w.show()
    sys.exit(app.exec())

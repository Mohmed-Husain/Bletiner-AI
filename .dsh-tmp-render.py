
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\\Windows\\Fonts")
sys.path.insert(0, r"C:\Users\Husain\Documents\Bletiner AI Desktop")
from PySide6.QtWidgets import QApplication
from bletiner.ui import theme
from bletiner.ui.desktop import build_main_window

app = QApplication.instance() or QApplication([])
app.setApplicationName("Bletiner AI")
theme.apply_dark_theme(app)
win = build_main_window()
win.resize(1672, 941)
win.show()
app.processEvents()
for key in ["Investigation","Sessions","Targets","Models","Settings"]:
    win.switch_to_tab(key)
    app.processEvents()
    win.grab().save(r"C:\Users\Husain\Documents\Bletiner AI Desktop\.dsh-tmp\real-" + key.lower() + ".png")
    print("saved", key)


import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List

from PyQt5 import QtCore, QtGui, QtWidgets, uic

# ---- Camoufox (sync API) ----------------------------------------------------
try:
    from camoufox.sync_api import Camoufox
    CAMOUFOX_OK = True
except Exception:
    CAMOUFOX_OK = False

PROFILES_FILE = "profiles.json"


# ===== Data models =====
@dataclass
class ProxyConfig:
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""

    def to_proxy_dict(self) -> Optional[Dict[str, Any]]:
        if not self.host or not self.port:
            return None
        d = {"server": f"http://{self.host}:{self.port}"}
        if self.username:
            d["username"] = self.username
        if self.password:
            d["password"] = self.password
        return d


@dataclass
class Profile:
    name: str = "Profile"
    viewport_width: int = 1280
    viewport_height: int = 800
    fullscreen: bool = False
    persistent_dir: str = ""
    use_geoip: bool = False
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["proxy"] = asdict(self.proxy)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Profile":
        raw_proxy = d.get("proxy", {})
        if not isinstance(raw_proxy, dict):
            raw_proxy = {}
        name = d.get("name", "Profile")

        persistent_dir = d.get("persistent_dir", "")
        if not persistent_dir:
            persistent_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "profiles", name
            )

        return Profile(
            name=name,
            viewport_width=int(d.get("viewport_width", 1280)),
            viewport_height=int(d.get("viewport_height", 800)),
            fullscreen=bool(d.get("fullscreen", False)),
            persistent_dir=persistent_dir,
            use_geoip=bool(d.get("use_geoip", False)),
            proxy=ProxyConfig(
                host=raw_proxy.get("host", ""),
                port=int(raw_proxy.get("port", 0) or 0),
                username=raw_proxy.get("username", ""),
                password=raw_proxy.get("password", ""),
            ),
        )


# ===== Persistence =====
def load_profiles() -> List[Profile]:
    if not os.path.exists(PROFILES_FILE):
        return []
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Profile.from_dict(x) for x in raw]


def save_profiles(profiles: List[Profile]) -> None:
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in profiles], f, indent=2)


# ===== Worker thread =====
class CamoufoxWorker(QtCore.QThread):
    started_ok = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    stopped = QtCore.pyqtSignal(str)

    def __init__(self, profile: Profile, launch_size: Optional[tuple[int,int]]=None, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.launch_size = launch_size
        self._stop = False
        self._ctx = None
        self.page = None
        self._nav_url: Optional[str] = None

    def run(self):
        if not CAMOUFOX_OK:
            self.error.emit("Camoufox not available. Install with: pip install -U 'camoufox[geoip]' and run 'camoufox fetch'.")
            return
        try:
            W, H = self.launch_size if self.launch_size else (self.profile.viewport_width, self.profile.viewport_height)
            opts: Dict[str, Any] = {
                "headless": False,  # GUI app; we use fullscreen instead
                "window": (W + 2, H + 88),
            }

            if self.profile.persistent_dir:
                os.makedirs(self.profile.persistent_dir, exist_ok=True)
                opts["persistent_context"] = True
                opts["user_data_dir"] = os.path.abspath(self.profile.persistent_dir)

            px = self.profile.proxy.to_proxy_dict()
            if px:
                opts["proxy"] = px
                if self.profile.use_geoip:
                    opts["geoip"] = True

            self._ctx = Camoufox(**opts).__enter__()

            pages = list(getattr(self._ctx, "pages", []))
            if pages:
                page = pages[0]
                for extra in pages[1:]:
                    try: extra.close()
                    except Exception: pass
            else:
                page = self._ctx.new_page()

            self.page = page

            # Show a start page with profile name for easy identification
            colors = ["#4c6ef5", "#2f9e44", "#e8590c", "#862e9c", "#1971c2", "#c2255c"]
            color = colors[hash(self.profile.name) % len(colors)]
            start_html = f"""data:text/html,<!DOCTYPE html>
<html><head><title>{self.profile.name}</title>
<style>body{{margin:0;background:#1a1a2e;display:flex;align-items:center;
justify-content:center;height:100vh;font-family:system-ui,sans-serif;}}
.badge{{background:{color};color:#fff;padding:24px 48px;border-radius:16px;
font-size:32px;font-weight:700;letter-spacing:2px;box-shadow:0 8px 32px rgba(0,0,0,.4);}}
.hint{{position:fixed;bottom:24px;color:#555;font-size:13px;}}
</style></head><body>
<div class="badge">{self.profile.name}</div>
<div class="hint">在地址栏输入网址开始浏览</div>
</body></html>"""
            try:
                page.goto(start_html)
            except Exception:
                pass

            # Set viewport; if fullscreen, try to match W,H (already set above)
            try:
                page.set_viewport_size({"width": W, "height": H})
            except Exception:
                pass

            # Try F11 for true fullscreen if the browser honors it
            if self.profile.fullscreen:
                try:
                    page.keyboard.press("F11")
                except Exception:
                    pass

            self.started_ok.emit(f"Session started for '{self.profile.name}'.")
            while not self._stop:
                if self._nav_url:
                    url = self._nav_url
                    self._nav_url = None
                    try:
                        self.page.goto(url)
                    except Exception:
                        pass
                time.sleep(0.2)

        except Exception as e:
            self.error.emit(f"Failed to start Camoufox: {e}")
        finally:
            try:
                if self._ctx is not None:
                    self._ctx.close()
                    try:
                        self._ctx.__exit__(None, None, None)
                    except Exception:
                        pass
            except Exception as e:
                self.error.emit(f"Error while stopping session: {e}")
            self.stopped.emit(f"Session stopped for '{self.profile.name}'.")

    def request_stop(self):
        self._stop = True

    def navigate(self, url: str):
        self._nav_url = url


# ===== MainWindow Controller =====
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("camoufox_manager.ui", self)

        # Widgets from UI
        self.profileList: QtWidgets.QListWidget
        self.newProfileButton: QtWidgets.QPushButton
        self.deleteProfileButton: QtWidgets.QPushButton
        self.nameEdit: QtWidgets.QLineEdit
        self.spinW: QtWidgets.QSpinBox
        self.spinH: QtWidgets.QSpinBox
        self.fullscreenCheck: QtWidgets.QCheckBox
        self.proxyHostEdit: QtWidgets.QLineEdit
        self.proxyPortSpin: QtWidgets.QSpinBox
        self.proxyUserEdit: QtWidgets.QLineEdit
        self.proxyPassEdit: QtWidgets.QLineEdit
        self.geoipCheck: QtWidgets.QCheckBox
        self.storageEdit: QtWidgets.QLineEdit
        self.browseStorageButton: QtWidgets.QPushButton
        self.saveButton: QtWidgets.QPushButton
        self.fingerprintButton: QtWidgets.QPushButton
        self.launchButton: QtWidgets.QPushButton
        self.stopButton: QtWidgets.QPushButton

        # State
        self.profiles: List[Profile] = load_profiles()
        self.current_index: int = -1
        self.workers: Dict[str, CamoufoxWorker] = {}  # profile name → worker

        # Signals
        self.profileList.itemSelectionChanged.connect(self._on_select_profile)
        self.newProfileButton.clicked.connect(self._new_profile)
        self.deleteProfileButton.clicked.connect(self._delete_profile)
        self.saveButton.clicked.connect(self._save_changes)
        self.browseStorageButton.clicked.connect(self._browse_storage)
        self.fingerprintButton.clicked.connect(self._check_fingerprint)
        self.launchButton.clicked.connect(self._launch)
        self.stopButton.clicked.connect(self._stop)

        self.launchButton.setObjectName("primary")
        self.stopButton.setObjectName("danger")
        self.fingerprintButton.setObjectName("info")

        # Initial UI state
        self._refresh_list()
        if self.profiles:
            self.profileList.setCurrentRow(0)
        self._update_buttons()

        # Professional theme
        QtWidgets.QApplication.setStyle("Fusion")
        self._apply_palette()
        self.statusbar.showMessage("Ready")

    # ----- Styling
    def _apply_palette(self):
        p = QtGui.QPalette()
        base = QtGui.QColor(248, 249, 251)
        text = QtGui.QColor(33, 37, 41)
        highlight = QtGui.QColor(76, 110, 245)
        p.setColor(QtGui.QPalette.Window, base)
        p.setColor(QtGui.QPalette.Base, QtGui.QColor(255, 255, 255))
        p.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(245, 246, 248))
        p.setColor(QtGui.QPalette.WindowText, text)
        p.setColor(QtGui.QPalette.Text, text)
        p.setColor(QtGui.QPalette.ButtonText, text)
        p.setColor(QtGui.QPalette.Highlight, highlight)
        p.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
        self.setPalette(p)

    # ----- Helpers
    def _refresh_list(self):
        self.profileList.clear()
        for p in self.profiles:
            running = p.name in self.workers
            item = QtWidgets.QListWidgetItem(("▶ " if running else "   ") + p.name)
            if running:
                item.setForeground(QtGui.QColor(76, 175, 80))
            self.profileList.addItem(item)

    def _update_buttons(self):
        prof = self._current()
        if prof:
            running = prof.name in self.workers
            self.launchButton.setEnabled(not running)
            self.stopButton.setEnabled(running)
            self.fingerprintButton.setEnabled(running)
        else:
            self.launchButton.setEnabled(False)
            self.stopButton.setEnabled(False)
            self.fingerprintButton.setEnabled(False)

    def _current(self) -> Optional[Profile]:
        if 0 <= self.current_index < len(self.profiles):
            return self.profiles[self.current_index]
        return None

    def _populate_form(self, p: Optional[Profile]):
        if not p:
            self.nameEdit.setText("")
            self.spinW.setValue(1280); self.spinH.setValue(800)
            self.fullscreenCheck.setChecked(False)
            self.proxyHostEdit.setText(""); self.proxyPortSpin.setValue(0)
            self.proxyUserEdit.setText(""); self.proxyPassEdit.setText("")
            self.geoipCheck.setChecked(False)
            self.storageEdit.setText("")
            return
        self.nameEdit.setText(p.name)
        self.spinW.setValue(p.viewport_width)
        self.spinH.setValue(p.viewport_height)
        self.fullscreenCheck.setChecked(p.fullscreen)
        self.proxyHostEdit.setText(p.proxy.host)
        self.proxyPortSpin.setValue(p.proxy.port)
        self.proxyUserEdit.setText(p.proxy.username)
        self.proxyPassEdit.setText(p.proxy.password)
        self.geoipCheck.setChecked(p.use_geoip)
        self.storageEdit.setText(p.persistent_dir)

    def _gather_form(self) -> Profile:
        p = self._current() or Profile()
        p.name = self.nameEdit.text().strip() or "Profile"
        p.viewport_width = int(self.spinW.value())
        p.viewport_height = int(self.spinH.value())
        p.fullscreen = self.fullscreenCheck.isChecked()
        p.proxy.host = self.proxyHostEdit.text().strip()
        p.proxy.port = int(self.proxyPortSpin.value())
        p.proxy.username = self.proxyUserEdit.text().strip()
        p.proxy.password = self.proxyPassEdit.text().strip()
        p.use_geoip = self.geoipCheck.isChecked()
        s = self.storageEdit.text().strip()
        if not s:
            s = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "profiles", p.name
            )
        p.persistent_dir = s
        return p

    # ----- Slots
    def _on_select_profile(self):
        self.current_index = self.profileList.currentRow()
        self._populate_form(self._current())
        self._update_buttons()

    def _new_profile(self):
        p = Profile(name=f"Profile {len(self.profiles)+1}")
        p.persistent_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "profiles", p.name
        )
        self.profiles.append(p)
        save_profiles(self.profiles)
        self._refresh_list()
        self.profileList.setCurrentRow(len(self.profiles)-1)
        self.statusbar.showMessage("New profile created", 3000)

    def _delete_profile(self):
        row = self.profileList.currentRow()
        if row < 0:
            return
        name = self.profiles[row].name
        if QtWidgets.QMessageBox.question(self, "Confirm Delete", f"Delete profile '{name}'?") != QtWidgets.QMessageBox.Yes:
            return
        del self.profiles[row]
        save_profiles(self.profiles)
        self._refresh_list()
        self._populate_form(None)
        self.current_index = -1
        self.statusbar.showMessage(f"Deleted '{name}'", 3000)

    def _save_changes(self):
        if self.current_index == -1:
            self._new_profile()
            return
        self.profiles[self.current_index] = self._gather_form()
        save_profiles(self.profiles)
        self._refresh_list()
        self.profileList.setCurrentRow(self.current_index)
        self.statusbar.showMessage("Profile saved", 3000)

    def _check_fingerprint(self):
        prof = self._current()
        if not prof:
            return
        worker = self.workers.get(prof.name)
        if not worker:
            QtWidgets.QMessageBox.information(self, "未运行", f"请先启动 '{prof.name}' 的 Session。")
            return
        worker.navigate("https://browserleaks.com/canvas")
        self.statusbar.showMessage(f"正在打开指纹检测页面: {prof.name}", 4000)

    def _browse_storage(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Storage Directory")
        if d:
            self.storageEdit.setText(d)

    def _launch(self):
        if self.current_index == -1:
            QtWidgets.QMessageBox.information(self, "No profile", "Create or select a profile first.")
            return

        prof = self._gather_form()
        if prof.name in self.workers:
            self.statusbar.showMessage(f"'{prof.name}' is already running", 3000)
            return

        self.profiles[self.current_index] = prof
        save_profiles(self.profiles)

        if not CAMOUFOX_OK:
            QtWidgets.QMessageBox.warning(self, "Camoufox not available",
                                          "Install with:\n  pip install -U 'camoufox[geoip]'\nThen run:\n  camoufox fetch")
            return

        if prof.fullscreen:
            screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
            launch_size = (screen.width(), screen.height())
        else:
            launch_size = (prof.viewport_width, prof.viewport_height)

        worker = CamoufoxWorker(prof, launch_size)
        worker.started_ok.connect(lambda m: self.statusbar.showMessage(m, 5000))
        worker.error.connect(lambda m: QtWidgets.QMessageBox.critical(self, "Session Error", m))
        worker.stopped.connect(self._on_stopped)
        self.workers[prof.name] = worker
        worker.start()
        self._refresh_list()
        self.profileList.setCurrentRow(self.current_index)
        self._update_buttons()

    def _stop(self):
        prof = self._current()
        if not prof:
            return
        worker = self.workers.get(prof.name)
        if worker and worker.isRunning():
            worker.request_stop()
            worker.wait(5000)
            self.statusbar.showMessage(f"Stopping '{prof.name}'…", 3000)
        else:
            self.statusbar.showMessage("No session to stop", 3000)

    def _on_stopped(self, msg: str):
        # find which profile stopped by matching name in message
        for name in list(self.workers.keys()):
            if name in msg:
                del self.workers[name]
                break
        self.statusbar.showMessage(msg, 5000)
        self._refresh_list()
        if self.current_index >= 0:
            self.profileList.setCurrentRow(self.current_index)
        self._update_buttons()

def apply_qss(app, path="dark.qss"):
    full = os.path.abspath(path)
    if not os.path.exists(full):
        raise FileNotFoundError(f"QSS not found: {full}")
    with open(full, "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

def main():
    # HiDPI before QApplication
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    apply_qss(app, "dark.qss")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

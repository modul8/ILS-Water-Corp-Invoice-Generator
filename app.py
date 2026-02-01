import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor

from services.settings_store import SettingsStore
from ui.shell import AppShell

APP_VERSION = "2.1.0"


def apply_dark_palette(app: QApplication) -> None:
    # Force Fusion so the palette is respected consistently on Windows
    app.setStyle("Fusion")

    p = QPalette()

    # Core surfaces
    p.setColor(QPalette.Window, QColor("#1e1e1e"))
    p.setColor(QPalette.WindowText, QColor("#ffffff"))
    p.setColor(QPalette.Base, QColor("#232323"))
    p.setColor(QPalette.AlternateBase, QColor("#2a2a2a"))
    p.setColor(QPalette.Text, QColor("#ffffff"))

    # Buttons
    p.setColor(QPalette.Button, QColor("#2b2f33"))
    p.setColor(QPalette.ButtonText, QColor("#ffffff"))

    # Tooltips
    p.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipText, QColor("#000000"))

    # Links (optional)
    p.setColor(QPalette.Link, QColor("#4c8bf5"))
    p.setColor(QPalette.LinkVisited, QColor("#4c8bf5"))

    # Selection
    p.setColor(QPalette.Highlight, QColor("#4c8bf5"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))

    app.setPalette(p)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("ILS Water Corp Invoice Console")
    app.setOrganizationName("SeanTools")

    apply_dark_palette(app)

    store = SettingsStore()
    shell = AppShell(store, app_version=APP_VERSION)
    shell.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class PlaceholderScreen(QWidget):
    def __init__(self, title: str, hint: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        t = QLabel(title)
        t.setStyleSheet("font-size: 20px; font-weight: 700;")
        h = QLabel(hint)
        h.setStyleSheet("color: #666;")
        h.setWordWrap(True)
        layout.addWidget(t)
        layout.addWidget(h)
        layout.addStretch(1)

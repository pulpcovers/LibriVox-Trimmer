import sys

from PySide6.QtWidgets import QApplication

from librivox_trimmer.main_window import MainWindow


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("LibriVox Trimmer")
    application.setOrganizationName("Pulp Covers")
    window = MainWindow()
    window.show()
    return application.exec()
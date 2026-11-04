from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox as QtMessageBox


class QMessageBox(QtMessageBox):
    @classmethod
    def _show(cls, parent, title, text, icon, buttons, default_button, *, details=""):
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setIcon(icon)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(text)
        dialog.setStandardButtons(buttons)
        if default_button != cls.StandardButton.NoButton:
            dialog.setDefaultButton(default_button)
        if details:
            dialog.setDetailedText(details)
        return cls.StandardButton(dialog.exec())

    @classmethod
    def warning(cls, parent, title, text, buttons=QtMessageBox.StandardButton.Ok,
                default_button=QtMessageBox.StandardButton.NoButton, *, details=""):
        return cls._show(parent, title, text, cls.Icon.Warning, buttons, default_button, details=details)

    @classmethod
    def critical(cls, parent, title, text, buttons=QtMessageBox.StandardButton.Ok,
                 default_button=QtMessageBox.StandardButton.NoButton, *, details=""):
        return cls._show(parent, title, text, cls.Icon.Critical, buttons, default_button, details=details)

    @classmethod
    def information(cls, parent, title, text, buttons=QtMessageBox.StandardButton.Ok,
                    default_button=QtMessageBox.StandardButton.NoButton, *, details=""):
        return cls._show(parent, title, text, cls.Icon.Information, buttons, default_button, details=details)

    @classmethod
    def question(cls, parent, title, text,
                 buttons=QtMessageBox.StandardButton.Yes | QtMessageBox.StandardButton.No,
                 default_button=QtMessageBox.StandardButton.NoButton):
        return cls._show(parent, title, text, cls.Icon.Question, buttons, default_button)
from dataclasses import dataclass
import json

from humbug.language.language_code import LanguageCode


@dataclass
class MindspaceSettings:
    use_soft_tabs: bool = True
    tab_size: int = 4
    font_size: float = None  # None means use the default font size
    auto_backup: bool = False  # Default to off
    auto_backup_interval: int = 300  # Default 5 minutes in seconds
    language: LanguageCode = LanguageCode.EN

    @classmethod
    def load(cls, path: str) -> "MindspaceSettings":
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            editor = data.get("editor", {})
            language_code = editor.get("language", "EN")
            return cls(
                use_soft_tabs=editor.get("useSoftTabs", True),
                tab_size=editor.get("tabSize", 4),
                font_size=editor.get("fontSize", None),
                auto_backup=editor.get("autoBackup", False),
                auto_backup_interval=editor.get("autoBackupInterval", 300),
                language=LanguageCode[language_code]
            )

    def save(self, path: str) -> None:
        data = {
            "editor": {
                "useSoftTabs": self.use_soft_tabs,
                "tabSize": self.tab_size,
                "fontSize": self.font_size,
                "autoBackup": self.auto_backup,
                "autoBackupInterval": self.auto_backup_interval,
                "language": self.language.name
            }
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

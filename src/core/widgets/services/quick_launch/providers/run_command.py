"""
Run Command Quick Launch Provider

Runs an executable (with optional arguments) the way the Windows Run dialog
does. The first token of the query is treated as the program and resolved to a
full path against, in order:

    1. an explicit absolute/relative path,
    2. the ``PATH`` environment variable (via ``shutil.which``, which honours
       ``PATHEXT`` so ``python`` finds ``python.exe``),
    3. the Windows ``App Paths`` registry key (e.g. ``code``, ``chrome``).

The remaining tokens are passed through as arguments. Everything is launched
through ``ShellExecute`` so YASB itself never spawns a child process or a
pipe - the shell owns the new process.
"""

import logging
import os
import shutil
import winreg

from core.utils.shell_utils import shell_open
from core.widgets.services.quick_launch.base_provider import (
    BaseProvider,
    ProviderMenuAction,
    ProviderMenuActionResult,
    ProviderResult,
)
from core.widgets.services.quick_launch.providers.resources.icons import ICON_RUN_COMMAND, ICON_WARNING

_APP_PATHS_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def _split_command(query: str) -> tuple[str, str]:
    """Split a query into ``(command, argument_string)``.

    Supports a quoted first token so program paths with spaces work:
        ``"C:\\Program Files\\app\\a.exe" --flag`` -> ``(C:\\...\\a.exe, --flag)``
    """
    query = query.strip()
    if query.startswith('"'):
        end = query.find('"', 1)
        if end != -1:
            return query[1:end], query[end + 1 :].strip()
        return query[1:].strip(), ""
    parts = query.split(None, 1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


def _resolve_app_paths(name: str) -> str | None:
    """Resolve a bare command via the Windows ``App Paths`` registry key."""
    candidates = [name] if name.lower().endswith(".exe") else [name, name + ".exe"]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for candidate in candidates:
            try:
                with winreg.OpenKey(hive, rf"{_APP_PATHS_SUBKEY}\{candidate}") as key:
                    val, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if val:
                val = os.path.expandvars(val.strip('"'))
                if os.path.isfile(val):
                    return os.path.abspath(val)
    return None


def _resolve_command(cmd: str) -> str | None:
    """Resolve *cmd* to a full executable path, or ``None`` if not found."""
    if not cmd:
        return None
    expanded = os.path.expandvars(os.path.expanduser(cmd))
    # Explicit absolute or relative path to an existing file
    if os.path.isfile(expanded):
        return os.path.abspath(expanded)
    # PATH lookup (honours PATHEXT: "python" -> python.exe)
    found = shutil.which(expanded)
    if found and os.path.isfile(found):
        return os.path.abspath(found)
    # App Paths registry (e.g. "code", "chrome")
    return _resolve_app_paths(cmd)


class RunCommandProvider(BaseProvider):
    """Run an executable resolved from PATH, with optional arguments."""

    name = "run_command"
    display_name = "Run Command"
    input_placeholder = "Run a command, e.g. ping 8.8.8.8"
    icon = ICON_RUN_COMMAND

    def get_results(self, text: str, **kwargs) -> list[ProviderResult]:
        query = self.get_query_text(text).strip()
        if not query:
            return [
                ProviderResult(
                    title="Run a command…",
                    description="Type a program and arguments, e.g. notepad, ping 8.8.8.8, code .",
                    icon_char=ICON_RUN_COMMAND,
                    provider=self.name,
                )
            ]

        cmd, args = _split_command(query)
        resolved = _resolve_command(cmd)

        if resolved:
            description = f"{resolved} {args}".strip() if args else resolved
            icon = ICON_RUN_COMMAND
        else:
            description = f"'{cmd}' not found in PATH — will still try to launch via the shell"
            icon = ICON_WARNING

        return [
            ProviderResult(
                title=f"Run: {query}",
                description=description,
                icon_char=icon,
                provider=self.name,
                action_data={"cmd": cmd, "args": args, "resolved": resolved or ""},
            )
        ]

    def execute(self, result: ProviderResult) -> bool | None:
        if not result.action_data.get("cmd"):
            # The empty-query hint tile: keep the popup open.
            return None
        self._launch(result, verb="open")
        return True

    def get_context_menu_actions(self, result: ProviderResult) -> list[ProviderMenuAction]:
        if not result.action_data.get("cmd"):
            return []
        actions = [
            ProviderMenuAction(id="run_as_admin", label="Run as administrator"),
            ProviderMenuAction(id="run_in_terminal", label="Run in terminal"),
        ]
        if result.action_data.get("resolved"):
            actions.append(ProviderMenuAction(id="copy_path", label="Copy resolved path", separator_before=True))
        return actions

    def execute_context_menu_action(self, action_id: str, result: ProviderResult) -> ProviderMenuActionResult:
        if action_id == "run_as_admin":
            self._launch(result, verb="runas")
            return ProviderMenuActionResult(close_popup=True)
        if action_id == "run_in_terminal":
            self._launch(result, verb="open", in_terminal=True)
            return ProviderMenuActionResult(close_popup=True)
        if action_id == "copy_path":
            from PyQt6.QtWidgets import QApplication

            resolved = result.action_data.get("resolved", "")
            clipboard = QApplication.clipboard()
            if clipboard and resolved:
                clipboard.setText(resolved)
        return ProviderMenuActionResult()

    @staticmethod
    def _launch(result: ProviderResult, verb: str = "open", in_terminal: bool = False) -> None:
        data = result.action_data
        target = data.get("resolved") or data.get("cmd", "")
        args = data.get("args", "")
        if not target:
            return
        try:
            if in_terminal:
                inner = f'"{target}" {args}'.strip()
                shell_open("cmd.exe", verb=verb, parameters=f"/k {inner}")
            else:
                shell_open(target, verb=verb, parameters=args or None)
        except Exception as e:
            logging.error("Run Command failed for '%s': %s", target, e)

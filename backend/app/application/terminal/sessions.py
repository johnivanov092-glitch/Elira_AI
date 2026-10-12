"""Shell tabs of the console panel; Elira has full control of them (John 2026-10-12).

One ConPTY shell per tab (pywinpty). Its output goes to the UI as is and into a
pyte screen, so Elira reads what the user sees. Prompt markers (OSC 133, as in
VS Code shell integration) tell where a command ends and with what exit code.
Elira runs commands here through run_bash(terminal=…): the same tool, approval
and Stop as any shell command; Stop sends Ctrl+C, the user's shell stays.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

_PROMPT = "\x1b]133;A"
_EXIT = re.compile(r"\x1b\]133;D;(\d+)")
_REPLAY_LIMIT = 400_000  # characters of raw output a reconnecting tab replays
_OUTPUT_LIMIT = 30_000  # characters of one command's output returned to Elira
_SESSION_LIMIT = 8
_READY_SECONDS = 20.0

# The pseudo-console starts its shell with Ctrl+C disabled (a new process group);
# children inherit that, so ^C never stopped ping -t. The shell turns it back on.
_ENABLE_CTRL_C = (
    "Add-Type -Name ConsoleCtrl -Namespace Elira -MemberDefinition '[DllImport(\"kernel32.dll\")] "
    "public static extern bool SetConsoleCtrlHandler(System.IntPtr handler, bool add);' | Out-Null;"
    "[Elira.ConsoleCtrl]::SetConsoleCtrlHandler([System.IntPtr]::Zero, $false) | Out-Null;"
)
_POWERSHELL_INIT = (
    _ENABLE_CTRL_C + "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
    "function global:prompt { $c = if ($?) { 0 } else { 1 }; "
    "\"$([char]27)]133;D;$c$([char]7)$([char]27)]133;A$([char]7)"
    "PS $($executionContext.SessionState.Path.CurrentLocation)> \" }"
)


def _git_bash() -> str:
    """Git for Windows bash (System32\\bash.exe is WSL, a different world)."""
    for candidate in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
                      Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    git = shutil.which("git")
    if git:
        bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if bash.is_file():
            return str(bash)
    return ""


def profiles() -> dict[str, dict[str, Any]]:
    """Shells the panel offers: id → title, argv, extra environment."""
    found: dict[str, dict[str, Any]] = {
        "powershell": {"title": "PowerShell",
                       "argv": ["powershell.exe", "-NoLogo", "-NoExit", "-Command", _POWERSHELL_INIT], "env": {}},
        # cmd cannot turn ^C back on itself: a short PowerShell does, then starts it.
        "cmd": {"title": "cmd", "argv": ["powershell.exe", "-NoLogo", "-NoProfile", "-Command",
                                         _ENABLE_CTRL_C + " & cmd.exe /K 'chcp 65001>nul & prompt $E]133;A$E\\$P$G'"],
                "env": {}},
    }
    pwsh = shutil.which("pwsh")
    if pwsh:
        found["pwsh"] = {"title": "PowerShell 7",
                         "argv": [pwsh, "-NoLogo", "-NoExit", "-Command", _POWERSHELL_INIT], "env": {}}
    bash = _git_bash()
    if bash:
        found["bash"] = {"title": "Git Bash", "argv": [bash, "--login", "-i"],
                         "env": {"PROMPT_COMMAND": 'printf "\\033]133;D;%s\\007\\033]133;A\\007" $?'}}
    return found


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TerminalSession:
    """One shell in a pseudo-console; thread-safe; output fans out to listeners."""

    def __init__(self, profile: str, cwd: Path, *, cols: int = 120, rows: int = 30, title: str = "") -> None:
        import pyte
        from winpty import PtyProcess

        spec = profiles().get(profile)
        if spec is None:
            raise ValueError(f"unknown terminal profile: {profile}")
        self.id = uuid.uuid4().hex[:8]
        self.profile = profile
        self.title = title or spec["title"]
        self.cwd = str(cwd)
        self.created_at = time.time()
        env = {**os.environ, **spec["env"]} if spec["env"] else None
        self._proc = PtyProcess.spawn(spec["argv"], cwd=self.cwd, dimensions=(rows, cols), env=env)

        class _Screen(pyte.HistoryScreen):
            scrolled = 0

            def index(self) -> None:  # a line leaves the bottom: count it, rows keep absolute numbers
                if self.cursor.y == (self.margins.bottom if self.margins else self.lines - 1):
                    self.scrolled += 1
                super().index()

        self._screen = _Screen(cols, rows, history=5000)
        self._stream = pyte.Stream(self._screen)
        self._lock = threading.Condition()
        self._replay: list[str] = []
        self._replay_size = 0
        self._prompts = 0  # prompts shown so far (OSC 133;A)
        self._prompt_rows: list[int] = []  # absolute screen row of each prompt
        self._exits: list[int] = []  # exit codes reported (OSC 133;D)
        self._busy = False  # a command was entered and its prompt has not come back
        self._listeners: list[Callable[[str | None], None]] = []
        self._carry = ""  # an unfinished OSC held for the next read
        self._last_output = time.monotonic()
        self._settled = False  # the shell went quiet after its first prompt
        self._alive = True
        threading.Thread(target=self._read_loop, name=f"terminal-{self.id}", daemon=True).start()

    # -- output -------------------------------------------------------------
    def _row(self) -> int:
        return self._screen.scrolled + self._screen.cursor.y

    def _feed(self, data: str) -> None:
        """Feed the screen, noting the row and exit code at each prompt marker."""
        data, self._carry = self._carry + data, ""
        start = data.rfind("\x1b")
        if start != -1 and data[start:start + 2] in ("\x1b", "\x1b]") and "\x07" not in data[start:] \
                and "\x1b\\" not in data[start + 1:]:
            # An OSC cut between two reads: a marker must not be missed.
            data, self._carry = data[:start], data[start:]
        pieces = data.split(_PROMPT)
        for index, piece in enumerate(pieces):
            self._exits.extend(int(code) for code in _EXIT.findall(piece))
            self._stream.feed(piece)
            if index < len(pieces) - 1:
                row = self._row() + (1 if self._screen.cursor.x else 0)
                self._prompt_rows.append(row)
                self._prompts += 1
                self._busy = False
                self._stream.feed(_PROMPT)

    def _read_loop(self) -> None:
        while True:
            try:
                data = self._proc.read(65536)
            except EOFError:
                break
            except Exception:
                if not self._proc.isalive():
                    break
                continue
            if not data:
                if not self._proc.isalive():
                    break
                continue
            with self._lock:
                self._last_output = time.monotonic()
                self._replay.append(data)
                self._replay_size += len(data)
                while self._replay_size > _REPLAY_LIMIT and len(self._replay) > 1:
                    self._replay_size -= len(self._replay.pop(0))
                self._feed(data)
                listeners = list(self._listeners)
                self._lock.notify_all()
            for listener in listeners:
                listener(data)
        with self._lock:
            self._alive = False
            listeners = list(self._listeners)
            self._lock.notify_all()
        for listener in listeners:
            listener(None)

    def subscribe(self, listener: Callable[[str | None], None]) -> tuple[str, Callable[[], None]]:
        """Replay of the output so far and live chunks after it, with no gap (None = shell ended)."""
        with self._lock:
            self._listeners.append(listener)
            replay = "".join(self._replay)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return replay, unsubscribe

    def _lines(self) -> tuple[int, list[str]]:
        screen = self._screen
        history = ["".join(line[x].data for x in range(screen.columns)).rstrip() for line in screen.history.top]
        return screen.scrolled - len(history), history + [line.rstrip() for line in screen.display]

    def read(self, lines: int = 80) -> str:
        """The last rows of the screen as the user sees them."""
        with self._lock:
            return self.read_locked(lines)

    def read_locked(self, lines: int) -> str:
        _, rows = self._lines()
        while rows and not rows[-1]:
            rows.pop()
        return "\n".join(rows[-lines:])

    # -- input --------------------------------------------------------------
    def write(self, data: str) -> None:
        if "\r" in data:
            with self._lock:
                self._busy = True
        self._proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(20, min(int(cols), 400)), max(5, min(int(rows), 200))
        with self._lock:
            self._screen.resize(rows, cols)
        self._proc.setwinsize(rows, cols)

    def run(self, command: str, should_stop: Callable[[], bool] = lambda: False) -> dict[str, Any]:
        """Type a command, wait for the next prompt, return what it printed."""
        with self._lock:
            ready = self._lock.wait_for(lambda: self._prompts > 0 or not self._alive, timeout=_READY_SECONDS)
            if not self._alive:
                return {"ok": False, "error": "terminal_closed", "text": "Терминал закрыт."}
            if not ready:
                return {"ok": False, "error": "terminal_not_ready", "text": "Оболочка терминала не ответила."}
            if self._busy:
                return {"ok": False, "error": "terminal_busy",
                        "text": "В терминале ещё идёт команда. Экран:\n" + self.read_locked(15)}
            # A quiet shell first: PSReadLine shows the prompt before it reads keys (the
            # first typed characters were lost, "Привет" arrived as "ривет"), and a
            # cancelled line may still redraw a prompt that must not end this command.
            quiet = 0.15 if self._settled else 0.5
            self._lock.wait_for(lambda: time.monotonic() - self._last_output > quiet, timeout=4)
            self._settled = True
            before, start, exits = self._prompts, self._row(), len(self._exits)
            self._busy = True
        self._proc.write(command + "\r")
        stopped = False
        with self._lock:
            while self._prompts <= before and self._alive:
                if not stopped and should_stop():
                    stopped = True
                    self._proc.write("\x03")
                    if not self._lock.wait_for(lambda: self._prompts > before or not self._alive, timeout=2):
                        # ^C did not reach the program (console mode, or it ignores ^C):
                        # end what the shell started; the shell itself stays.
                        self._lock.release()
                        try:
                            self.kill_running()
                        finally:
                            self._lock.acquire()
                        self._lock.wait_for(lambda: self._prompts > before or not self._alive, timeout=5)
                    # The ^C left in the input may still cancel a line and redraw the prompt.
                    self._lock.wait_for(lambda: time.monotonic() - self._last_output > 0.8, timeout=4)
                    break
                self._lock.wait(timeout=0.2)
            end = self._prompt_rows[before] if len(self._prompt_rows) > before else self._row() + 1
            dropped, rows = self._lines()
            exit_code = self._exits[exits] if len(self._exits) > exits else None
            alive = self._alive
        body = rows[max(0, start - dropped):max(0, end - dropped)]
        # The first rows echo the prompt and the command (wrapped when long).
        echoed, seen = 0, ""
        target = _compact(command)
        while echoed < len(body) and target and target not in _compact(seen):
            seen += body[echoed]
            echoed += 1
        if target not in _compact(seen):
            echoed = min(1, len(body))  # the echo did not render as typed: drop the prompt row only
        output = body[echoed:]
        while output and not output[-1]:
            output.pop()
        text = "\n".join(output)
        if len(text) > _OUTPUT_LIMIT:
            text = "…\n" + text[-_OUTPUT_LIMIT:]
        if stopped:
            return {"ok": False, "error": "cancelled", "text": text, "exit_code": exit_code}
        if not alive:
            return {"ok": False, "error": "terminal_closed", "text": text, "exit_code": exit_code}
        return {"ok": exit_code in (0, None), "text": text, "exit_code": exit_code}

    def kill_running(self) -> int:
        """End the programs the interactive shell started (not the shell); returns how many."""
        try:
            import psutil  # optional, as in run_bash: without it only ^C stops a command
        except ImportError:
            return 0
        try:
            shell = psutil.Process(self._proc.pid)
            if self.profile == "cmd":  # a PowerShell wrapper started the cmd.exe the user types into
                shell = next((child for child in shell.children() if child.name().lower() == "cmd.exe"), shell)
            running = [child for child in shell.children()
                       if child.name().lower() not in {"conhost.exe", "openconsole.exe"}]
        except psutil.Error:
            return 0
        for child in running:
            try:
                for item in child.children(recursive=True):
                    item.kill()
                child.kill()
            except psutil.Error:
                continue
        return len(running)

    # -- lifecycle ----------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self._alive and self._proc.isalive()

    def info(self) -> dict[str, Any]:
        with self._lock:
            busy = self._busy
        return {"id": self.id, "profile": self.profile, "title": self.title, "cwd": self.cwd,
                "busy": busy, "alive": self.alive}

    def close(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass


class TerminalManager:
    """The open tabs; "active" is the tab the user looks at (or the newest)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}
        self._active = ""

    def create(self, profile: str, cwd: Path | str | None = None, *, cols: int = 120, rows: int = 30,
               title: str = "") -> TerminalSession:
        folder = Path(cwd) if cwd and Path(cwd).is_dir() else Path.home()
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value.alive}
            if len(self._sessions) >= _SESSION_LIMIT:
                raise RuntimeError(f"открыто {_SESSION_LIMIT} терминалов — закройте лишние")
        session = TerminalSession(profile, folder, cols=cols, rows=rows, title=title)
        with self._lock:
            self._sessions[session.id] = session
            self._active = session.id
        return session

    def get(self, session_id: str = "active") -> TerminalSession | None:
        with self._lock:
            live = {key: value for key, value in self._sessions.items() if value.alive}
            if session_id in ("", "active"):
                if self._active in live:
                    return live[self._active]
                return max(live.values(), key=lambda item: item.created_at) if live else None
            return live.get(session_id)

    def set_active(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                self._active = session_id
                return True
            return False

    def close(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def close_all(self) -> None:
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            session.close()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
            active = self._active
        return [{**session.info(), "active": session.id == active} for session in sessions if session.alive]


manager = TerminalManager()


def open_tabs_note() -> str:
    """For the run's instructions, only while tabs are open: "the terminal" is this panel.

    Live run 6ec5e95a: «посмотри в терминале, что я проверил» started a fresh
    PowerShell instead of reading the user's tab.
    """
    tabs = manager.list()
    if not tabs:
        return ""
    shells = {key: value["title"] for key, value in profiles().items()}
    names = ", ".join(f"«{tab['title']}» {tab['id']} — {shells.get(tab['profile'], tab['profile'])}"
                      + (", активная" if tab["active"] else "") for tab in tabs)
    return (f"В панели «Консоль» открыт терминал пользователя: {names}. «Терминал/консоль» в просьбе — это он: "
            "что там сейчас — run_bash(command=\"\", terminal=\"active\"); команду, которую пользователь хочет "
            "видеть там, — run_bash(command=…, terminal=\"active\") прямо на языке этой оболочки "
            "(в PowerShell — без обёртки powershell.exe).")

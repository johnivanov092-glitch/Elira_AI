"""Privilege boundary for release bytes, not a general file-operation endpoint."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from elira_release import _contained, _tree_files
from foundation_windows import safe_read_file


_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)


def _relative(name: str) -> Path:
    """Reject Windows aliases, streams, devices and traversal before path joins."""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid publication file name")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or any(
        part in {".", ".."} or part.endswith((" ", ".")) or ":" in part
        or _RESERVED.fullmatch(part) for part in path.parts
    ) or path.as_posix() != name:
        raise ValueError("Publication requires canonical relative file names")
    return Path(*path.parts)


class FoundationStorage:
    def __init__(self, host, *, protected_root: Path, worker_root: Path):
        self.host = host
        self.protected_root = protected_root.resolve()
        self.worker_root = worker_root.resolve()

    def user_access(self):
        return self.host.impersonate()

    def _destination(self, destination: Path) -> Path:
        destination = _contained(destination, self.protected_root)
        if destination == self.protected_root or destination.exists():
            raise ValueError("Publication requires a new private destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def publish(self, source: Path, destination: Path, *, files=None) -> None:
        """Read with client rights; create fresh service-owned files and no links.

        Caller observes the final destination fingerprint. Inventory is never a
        supplied receipt. Each source handle is checked and denies concurrent
        write/delete sharing while its bytes are copied.
        """
        with self.user_access():
            source = source.resolve()
            names = list(files) if files is not None else [
                file.relative_to(source).as_posix() for file in _tree_files(source)
            ]
        relatives = [_relative(name) for name in names]
        keys = [str(path).casefold() for path in relatives]
        if len(keys) != len(set(keys)):
            raise ValueError("Publication contains conflicting Windows file names")
        destination = self._destination(destination)
        # Recovery must never observe snapshot.json before all database bytes.
        # The service is the only writer to this parent. Failed staging remains
        # private diagnostic evidence; it is never selected by release state.
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.publishing-", dir=destination.parent))
        for relative in relatives:
            target = _contained(staging / relative, self.protected_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Open under the client's token. Access checks and final-handle
            # validation happen there, not using the service's read privileges.
            with self.user_access():
                original = safe_read_file(source / relative, source)
                reader = original.__enter__()
            try:
                with target.open("xb") as output:
                    while True:
                        chunk = reader.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                original.__exit__(None, None, None)
        if destination.exists():
            raise ValueError("Publication destination appeared before activation")
        os.rename(staging, destination)

    def export(self, source: Path, destination: Path) -> None:
        """Expose a selected private backup to the unprivileged restore worker.

        The service opens only its own fixed source. Destination creation and
        every write run with the client token, including if user paths race.
        """
        source = _contained(source, self.protected_root)
        names = [_relative(file.relative_to(source).as_posix()) for file in _tree_files(source)]
        with self.user_access():
            destination = _contained(destination, self.worker_root)
            if destination == self.worker_root:
                raise ValueError("Export requires a dedicated worker directory")
            destination.mkdir(parents=True, exist_ok=False)
        for relative in names:
            with (source / relative).open("rb") as original:
                with self.user_access():
                    target = _contained(destination / relative, self.worker_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as output:
                        while True:
                            chunk = original.read(1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())

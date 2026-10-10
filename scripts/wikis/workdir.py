"""Work directory, crash journal and run locks of ``fetch_wikis.py``.

The work directory (journal and lock) is private: by default ``<root>/.cache/fetch_wikis/work`` (git-ignored), created
mode 0700; a directory owned by another user, a symlink, or world-writable is refused, lock and journal files are
opened without following symlinks, and a journal records the root it belongs to in its first line (a journal of
another root, an unknown event or a corrupt line before the last is refused here; an event that contradicts the
manifest is refused by :meth:`wikis.acquire.State.replay_problem`).

Every run on a root also takes ``<root>/.cache/fetch_wikis/root.lock`` and registers its work directory there, so an
interrupted run's journal in another work directory of the same root blocks new runs until it is resumed.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

STATE_DIR = ".cache/fetch_wikis"  # git-ignored, inside --root: the root lock, the registry and the default work dir
JOURNAL_FORMAT = "dynamix-fetch-wikis-journal/1"
JOURNAL_EVENTS = ("asset", "asset_status", "failure", "clear_failures", "candidate", "withdraw")


class LockHeld(RuntimeError):
    pass


class UnsafeWorkDir(RuntimeError):
    """The work directory, its journal or a lock file cannot be used safely (another owner, a symlink, a journal of
    another root, a corrupt or contradictory journal)."""


def default_work_dir(root: Path) -> Path:
    """``<root>/.cache/fetch_wikis/work``: git-ignored and private to the repository owner (never a shared temp
    directory, where another local user could plant a journal or a lock symlink)."""
    return root.resolve() / STATE_DIR / "work"


def root_lock_path(root: Path) -> Path:
    """The lock every run on ``root`` takes, whatever ``--work-dir``/``--lock-file`` say."""
    return root / STATE_DIR / "root.lock"


def _unsafe_dir_problem(path: Path) -> str | None:
    """Why ``path`` (an existing directory entry) is not a safe work directory, else None."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "does not exist"
    if stat.S_ISLNK(info.st_mode):
        return "is a symlink"
    if not stat.S_ISDIR(info.st_mode):
        return "is not a directory"
    if info.st_uid != os.getuid():
        return "is owned by another user"
    if info.st_mode & stat.S_IWOTH:
        return "is writable by other users"
    return None


def prepare_work_dir(work_dir: Path) -> Path:
    """Create ``work_dir`` (mode 0700) if needed and check that it is a real directory of the current user that other
    users cannot write; :class:`UnsafeWorkDir` otherwise."""
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(work_dir, 0o700)
    except FileExistsError:
        pass
    problem = _unsafe_dir_problem(work_dir)
    if problem:
        raise UnsafeWorkDir(f"work directory {work_dir} {problem}; choose another --work-dir")
    return work_dir


def _open_private(path: Path, flags: int, mode: int = 0o600) -> int:
    """``os.open`` without following a symlink at ``path``; the opened file must be a regular file of the current
    user with a single link (a planted hard link or device is refused)."""
    try:
        descriptor = os.open(path, flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), mode)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise UnsafeWorkDir(f"{path} is a symlink; refusing to open it") from None
        raise
    info = os.fstat(descriptor)
    problem = ("is not a regular file" if not stat.S_ISREG(info.st_mode) else
               "is owned by another user" if info.st_uid != os.getuid() else
               "has several hard links" if info.st_nlink != 1 else None)
    if problem:
        os.close(descriptor)
        raise UnsafeWorkDir(f"{path} {problem}; refusing to use it")
    return descriptor


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """``fcntl.flock`` on ``path``; :class:`LockHeld` (naming the holder's PID when recorded) if another run has it.
    The lock file is opened without following symlinks (see :func:`_open_private`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = _open_private(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise
            holder = os.pread(descriptor, 64, 0).decode("ascii", "replace").strip() or "unknown"
            raise LockHeld(f"another fetch_wikis.py run holds {path} (pid {holder}); monitor it instead of starting "
                           "a second writer") from None
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode(), 0)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def claim_work_dir(root: Path, work_dir: Path) -> None:
    """Register ``work_dir`` for ``root`` (call while holding the root lock); :class:`LockHeld` when another work
    directory used for this root still holds an interrupted run's journal (it must be resumed with that
    ``--work-dir``, or its downloads would become orphan files). Only safe directories whose journal header names
    this root count: a directory of another user, a symlink, or a journal of another root is not this root's."""
    registry = root / STATE_DIR / "work-dirs.txt"
    mine = str(work_dir.resolve())
    known = [line for line in (registry.read_text(encoding="utf-8").splitlines() if registry.is_file() else [])
             if line.strip()]
    for other in dict.fromkeys(known + [str(default_work_dir(root))]):
        if other == mine or _unsafe_dir_problem(Path(other)):
            continue
        journal = Journal(Path(other) / "journal.jsonl", root)
        if journal.belongs_to_root() and journal.has_events():
            raise LockHeld(f"an interrupted run on this root left its journal in {other}; resume it with "
                           f"--work-dir {other} before starting another run")
    if mine not in known:
        registry.parent.mkdir(parents=True, exist_ok=True)
        with open(registry, "a", encoding="utf-8") as stream:
            stream.write(mine + "\n")


class Journal:
    """Append-only JSON-lines event log of one root. The first line is a header naming the root; a torn last line
    (crash during a write) is ignored and cut off before the next append; any other unreadable line is an error."""

    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = str(Path(root).resolve())

    def _text(self) -> str:
        try:
            descriptor = _open_private(self.path, os.O_RDONLY)
        except FileNotFoundError:
            return ""
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read().decode("utf-8", errors="replace")

    def _lines(self) -> list[str]:
        """Complete lines (a last line without its newline is a torn write and left out)."""
        text = self._text()
        lines = text.split("\n")
        return [line for line in lines[:-1]]  # text ends with "\n": the last item is ""; else it is the torn tail

    def _header(self, lines: list[str]) -> dict | None:
        try:
            header = json.loads(lines[0]) if lines else None
        except ValueError:
            return None
        return header if isinstance(header, dict) and header.get("event") == "header" else None

    def belongs_to_root(self) -> bool:
        header = self._header(self._lines())
        return header is not None and header.get("root") == self.root

    def has_events(self) -> bool:
        return len(self._lines()) > 1

    def read(self) -> list[dict]:
        """The events after the header; :class:`UnsafeWorkDir` for a journal of another root or without a header, a
        corrupt line before the last, or an unknown event."""
        lines = self._lines()
        if not lines:
            return []
        header = self._header(lines)
        if header is None or header.get("format") != JOURNAL_FORMAT:
            raise UnsafeWorkDir(f"{self.path} has no {JOURNAL_FORMAT} header; it was not written by this tool for "
                                "this root (inspect it, then remove it or use another --work-dir)")
        if header.get("root") != self.root:
            raise UnsafeWorkDir(f"{self.path} belongs to the root {header.get('root')!r}, not {self.root!r}; resume "
                                "that run with its own --root, or use another --work-dir")
        events = []
        for number, line in enumerate(lines[1:], start=2):
            try:
                event = json.loads(line)
            except ValueError:
                raise UnsafeWorkDir(f"{self.path}: line {number} is corrupt (not a torn last line)") from None
            if not isinstance(event, dict) or event.get("event") not in JOURNAL_EVENTS:
                raise UnsafeWorkDir(f"{self.path}: line {number} is not a known journal event")
            events.append(event)
        return events

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = _open_private(self.path, os.O_RDWR | os.O_CREAT)
        try:
            size = os.fstat(descriptor).st_size
            if size and os.pread(descriptor, 1, size - 1) != b"\n":
                # A torn last line from a crash: cut it off so that this event starts on a line of its own.
                data = os.pread(descriptor, size, 0)
                size = data.rfind(b"\n") + 1
                os.ftruncate(descriptor, size)
            payload = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            if size == 0:
                payload = json.dumps({"event": "header", "format": JOURNAL_FORMAT, "root": self.root},
                                     ensure_ascii=False, sort_keys=True) + "\n" + payload
            os.pwrite(descriptor, payload.encode("utf-8"), size)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

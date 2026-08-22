"""Format-neutral transactional editing for one UTF-8 text file.

The buffer owns exact-text dirty state, undo/redo, auto-save mirroring,
external-change observation, rebasing, and preimage-safe saves. Callers supply
the atomic writer, auto-save location, and optional post-save observer, keeping
Org syntax, application logging, and UI policy outside this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


AtomicWriter = Callable[[Path, str], None]
SaveObserver = Callable[[str, str, Path], None]


@dataclass(frozen=True)
class BufferTransaction:
    """One logical, reversible exact-text edit."""

    before: str
    after: str
    description: str


@dataclass(frozen=True)
class DiskSignature:
    """Cheap change hint for a file whose contents remain authoritative."""

    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ExternalFileChange:
    """One disk state that cannot yet be absorbed into a dirty buffer."""

    kind: str
    path: Path
    text: str | None = None
    signature: DiskSignature | None = None
    detail: str | None = None

    @property
    def summary(self) -> str:
        if self.kind == "changed":
            return "contents changed externally"
        if self.kind == "missing":
            return "file is missing"
        suffix = f": {self.detail}" if self.detail else ""
        return f"file is unreadable{suffix}"


class BufferChangedError(OSError):
    """The real file no longer matches the buffer's saved preimage."""

    def __init__(self, path: Path, detail: str | None = None) -> None:
        message = f"{path}: changed since the editing buffer was loaded"
        if detail:
            message += f" ({detail})"
        super().__init__(message)
        self.path = path


class TextFileBuffer:
    """In-memory editing state for one UTF-8 text file.

    The real file changes only through :meth:`save`. Dirty states are mirrored
    to the caller-selected auto-save path through the supplied atomic writer.
    Each accepted edit is one undo transaction; save, discard, reload, and a
    successful external rebase establish explicit history boundaries.
    """

    def __init__(
        self,
        path: Path,
        *,
        autosave_path: Path,
        atomic_write: AtomicWriter,
        on_save: SaveObserver | None = None,
    ) -> None:
        self.path = path
        self.autosave_path = autosave_path
        self._atomic_write = atomic_write
        self._on_save = on_save
        self._saved_text = self._read_disk_text()
        self._text = self._saved_text
        self._disk_signature: DiskSignature | None = self._read_disk_signature()
        self.external_change: ExternalFileChange | None = None
        self.dirty = False
        self._undo_stack: list[BufferTransaction] = []
        self._redo_stack: list[BufferTransaction] = []

    def read(self) -> str:
        return self._text

    @property
    def saved_text(self) -> str:
        """Exact disk baseline used for stale checks and reconciliation."""
        return self._saved_text

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_count(self) -> int:
        return len(self._undo_stack)

    def apply_text(
        self,
        text: str,
        *,
        description: str = "Edit text file",
    ) -> bool:
        """Record one exact-text logical edit and refresh auto-save."""
        if text == self._text:
            return False
        transaction = BufferTransaction(self._text, text, description)
        self._undo_stack.append(transaction)
        self._redo_stack.clear()
        self._set_text(text)
        return True

    def recover(self, text: str) -> None:
        """Adopt recovered auto-save text as dirty buffer contents."""
        self._clear_history()
        if text != self._text:
            transaction = BufferTransaction(
                self._text,
                text,
                "Recover auto-save data",
            )
            self._undo_stack.append(transaction)
            self._set_text(text)

    def undo(self) -> str | None:
        """Undo one logical edit and return its description."""
        if not self._undo_stack:
            return None
        transaction = self._undo_stack.pop()
        self._redo_stack.append(transaction)
        self._set_text(transaction.before)
        return transaction.description

    def redo(self) -> str | None:
        """Redo one logical edit and return its description."""
        if not self._redo_stack:
            return None
        transaction = self._redo_stack.pop()
        self._undo_stack.append(transaction)
        self._set_text(transaction.after)
        return transaction.description

    def _set_text(self, text: str) -> None:
        self._text = text
        self.dirty = text != self._saved_text
        if self.dirty:
            self._atomic_write(self.autosave_path, text)
        else:
            self._remove_autosave()

    def _read_disk_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def _read_disk_signature(self) -> DiskSignature:
        stat = self.path.stat()
        return DiskSignature(
            device=stat.st_dev,
            inode=stat.st_ino,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )

    def _remember_external_problem(
        self,
        kind: str,
        exc: OSError | UnicodeError,
        signature: DiskSignature | None = None,
    ) -> ExternalFileChange:
        change = ExternalFileChange(
            kind=kind,
            path=self.path,
            signature=signature,
            detail=str(exc),
        )
        self.external_change = change
        return change

    def _adopt_disk_text(self, text: str, signature: DiskSignature) -> None:
        """Replace a clean buffer with a newly observed disk revision."""
        self._saved_text = text
        self._text = text
        self._disk_signature = signature
        self.external_change = None
        self.dirty = False
        self._clear_history()
        self._remove_autosave()

    def check_external_change(
        self, *, force: bool = False
    ) -> ExternalFileChange | None:
        """Observe disk changes without overwriting local edits.

        File metadata is only a polling hint. A changed hint triggers an exact
        read; ``force`` always performs that read and is required before save.
        Clean buffers adopt changed disk text. Dirty buffers retain Base/Ours
        and expose Theirs through :attr:`external_change` for reconciliation.
        """
        try:
            signature = self._read_disk_signature()
        except FileNotFoundError as exc:
            return self._remember_external_problem("missing", exc)
        except OSError as exc:
            return self._remember_external_problem("unreadable", exc)

        pending = self.external_change
        if not force:
            if pending is not None and pending.signature == signature:
                if self.dirty:
                    return pending
                if pending.kind == "changed" and pending.text is not None:
                    self._adopt_disk_text(pending.text, signature)
                    return None
                return pending
            if pending is None and signature == self._disk_signature:
                return None

        try:
            disk_text = self._read_disk_text()
        except (OSError, UnicodeError) as exc:
            return self._remember_external_problem(
                "unreadable", exc, signature
            )

        if disk_text == self._saved_text:
            self._disk_signature = signature
            self.external_change = None
            return None
        if not self.dirty:
            self._adopt_disk_text(disk_text, signature)
            return None

        change = ExternalFileChange(
            kind="changed",
            path=self.path,
            text=disk_text,
            signature=signature,
        )
        self.external_change = change
        return change

    def rebase_external_change(
        self,
        change: ExternalFileChange,
        merged_text: str,
        *,
        description: str = "Reapply edits after external changes",
    ) -> bool:
        """Adopt Theirs as Base and retain merged work as one transaction."""
        if change != self.external_change:
            raise ValueError("external change is no longer current")
        if change.kind != "changed" or change.text is None:
            raise ValueError(f"cannot rebase external state: {change.kind}")
        if change.signature is None:  # pragma: no cover - changed always has one
            raise ValueError("cannot rebase an external change without a signature")

        theirs = change.text
        self._saved_text = theirs
        self._text = theirs
        self._disk_signature = change.signature
        self.external_change = None
        self.dirty = False
        self._clear_history()
        if merged_text == theirs:
            self._remove_autosave()
            return False

        self._undo_stack.append(
            BufferTransaction(theirs, merged_text, description)
        )
        self._set_text(merged_text)
        return True

    def save(self) -> None:
        """Write only if the real file still matches the saved preimage."""
        change = self.check_external_change(force=True)
        if change is not None:
            raise BufferChangedError(self.path, change.summary)
        previous = self._saved_text
        self._atomic_write(self.path, self._text)
        self._saved_text = self._text
        try:
            self._disk_signature = self._read_disk_signature()
        except OSError:
            # The write succeeded; force an exact read on the next observation.
            self._disk_signature = None
        self.external_change = None
        self.dirty = False
        self._clear_history()
        self._remove_autosave()
        if self._on_save is not None:
            self._on_save(previous, self._text, self.path)

    def discard(self) -> None:
        """Drop pending changes and auto-save; leave the real file as-is."""
        self._text = self._saved_text
        self.dirty = False
        self._clear_history()
        self._remove_autosave()

    def reload(self) -> None:
        """Re-read the real file as clean and clear history."""
        self._saved_text = self._read_disk_text()
        self._text = self._saved_text
        self._disk_signature = self._read_disk_signature()
        self.external_change = None
        self.dirty = False
        self._clear_history()
        self._remove_autosave()

    def _clear_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()

    def _remove_autosave(self) -> None:
        try:
            self.autosave_path.unlink()
        except FileNotFoundError:
            pass

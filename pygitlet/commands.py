import dataclasses
import hashlib
import pickle
from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from textwrap import dedent
from typing import NamedTuple, Self

from frozendict import frozendict

from .errors import PyGitletException


@dataclass(frozen=True)
class Repository:
    gitlet: Path

    @property
    def commits(self) -> Path:
        return self.gitlet / "commits"

    @property
    def blobs(self) -> Path:
        return self.gitlet / "blobs"

    @property
    def stage(self) -> Path:
        return self.gitlet / "stage"

    @property
    def branches(self) -> Path:
        return self.gitlet / "branches"

    @property
    def current_branch(self) -> Path:
        return self.branches / ".current-branch"


class Diff(Enum):
    ADDED = auto()
    REMOVED = auto()
    CHANGED = auto()


@dataclass(frozen=True)
class Blob:
    name: Path
    contents: str
    diff: Diff

    @property
    def hash(self) -> str:
        return hashlib.sha1(self.contents.encode(encoding="utf-8")).hexdigest()


class Merge(NamedTuple):
    origin: "Commit"
    target: "Commit"


@dataclass(frozen=True)
class Commit:
    timestamp: datetime
    message: str
    parent: Self | Merge | None
    file_blob_map: frozendict[Path, Blob] = field(default_factory=frozendict)

    @property
    def hash(self) -> str:
        commit_serialized = pickle.dumps(self)
        return hashlib.sha1(commit_serialized).hexdigest()

    @property
    def is_merge_commit(self) -> bool:
        return isinstance(self.parent, Merge)


def write_commit(repo: Repository, commit: Commit) -> None:
    with (repo.commits / commit.hash).open(mode="wb") as f:
        pickle.dump(commit, f)


@dataclass(frozen=True)
class Branch:
    name: str
    commit: Commit
    is_current: bool


def write_branch(repo: Repository, branch: Branch) -> None:
    with (repo.branches / branch.name).open(mode="wb") as f:
        pickle.dump(branch, f)
    if branch.is_current:
        repo.current_branch.unlink(missing_ok=True)
        repo.current_branch.symlink_to(repo.branches / branch.name)


def get_current_branch(repo: Repository) -> Branch:
    with repo.current_branch.open(mode="rb") as f:
        return pickle.load(f)


def set_branch_commit(
    repo: Repository,
    branch: Branch,
    commit: Commit,
) -> None:
    branch = dataclasses.replace(branch, commit=commit)
    write_branch(repo, branch)


def init(repo: Repository) -> None:
    if repo.gitlet.exists():
        raise PyGitletException(
            dedent(
                """
            A Gitlet version-control system already exists in the current directory.
            """
            ).strip()
        )

    repo.gitlet.mkdir()
    repo.commits.mkdir()
    repo.blobs.mkdir()
    repo.stage.mkdir()
    repo.branches.mkdir()

    aware_unix_epoch = datetime.fromtimestamp(0, tz=timezone.utc).astimezone()
    init_commit = Commit(aware_unix_epoch, "initial commit", None)
    init_branch = Branch("main", init_commit, True)

    write_branch(repo, init_branch)
    write_commit(repo, init_commit)


def add(repo: Repository, file_path: Path) -> None:
    if not file_path.exists():
        raise PyGitletException("File does not exist.")

    with file_path.open() as f:
        contents = f.read()
    current_branch = get_current_branch(repo)

    blob = Blob(
        file_path,
        contents,
        (
            Diff.CHANGED
            if file_path in current_branch.commit.file_blob_map
            else Diff.ADDED
        ),
    )
    relative_path = file_path.relative_to(repo.gitlet.parent)
    stage_file_path = repo.stage / relative_path
    if stage_file_path.exists():
        with stage_file_path.open(mode="rb") as f:
            prev_blob: Blob = pickle.load(f)
        if prev_blob.hash == blob.hash:
            stage_file_path.unlink(missing_ok=True)
    else:
        with stage_file_path.open(mode="wb") as f:
            pickle.dump(blob, f)


def commit(repo: Repository, message: str) -> None:
    if list(repo.stage.iterdir()) == []:
        raise PyGitletException("No changes added to the commit.")
    elif message == "":
        raise PyGitletException("Please enter a commit message.")

    blob_dict = {}
    for k in repo.stage.iterdir():
        with k.open(mode="rb") as f:
            blob: Blob = pickle.load(f)
        if not (repo.blobs / blob.hash).exists() or blob.diff == Diff.REMOVED:
            with (repo.blobs / blob.hash).open(mode="wb") as f:
                pickle.dump(blob, f)
        blob_dict[blob.name] = blob.hash
        k.unlink()

    current_branch = get_current_branch(repo)
    commit = Commit(
        datetime.now().astimezone(),
        message,
        current_branch.commit,
        file_blob_map=frozendict(blob_dict),
    )
    write_commit(repo, commit)

    set_branch_commit(repo, current_branch, commit)


def remove(repo: Repository, file_path: Path) -> None:
    current_branch = get_current_branch(repo)
    if not (
        (repo.stage / file_path.name).exists()
        or file_path in current_branch.commit.file_blob_map
    ):
        raise PyGitletException("No reason to remove the file.")

    with file_path.open() as f:
        contents = f.read()
    current_branch = get_current_branch(repo)

    blob = Blob(file_path, contents, Diff.REMOVED)
    with (repo.stage / file_path.name).open(mode="wb") as f:
        pickle.dump(blob, f)

    file_path.unlink()


def format_commit(commit: Commit) -> str:
    timestamp_formatted = commit.timestamp.strftime("%a %b %-d %X %Y %z")
    if commit.is_merge_commit:
        origin, target = commit.parent
        message = f"===\ncommit {commit.hash}\nMerge: {origin.hash[:7]} {target.hash[:7]}\nDate: {timestamp_formatted}\n{commit.message}\n\n"
    else:
        message = f"===\ncommit {commit.hash}\nDate: {timestamp_formatted}\n{commit.message}\n\n"
    return message


def log(repo: Repository) -> str:
    current_commit = get_current_branch(repo).commit
    log = StringIO()
    while current_commit is not None:
        log.write(format_commit(current_commit))
        if current_commit.is_merge_commit:
            current_commit = current_commit.parent.origin
        else:
            current_commit = current_commit.parent
    log.seek(0)
    return log.read().strip()


def global_log(repo: Repository) -> str:
    log = StringIO()
    for serialized_commit_path in repo.commits.iterdir():
        with serialized_commit_path.open(mode="rb") as f:
            commit: Commit = pickle.load(f)
        log.write(format_commit(commit))
    log.seek(0)
    return log.read().strip()


def find(repo: Repository, message: str) -> str:
    filtered_list = []
    for serialized_commit_path in repo.commits.iterdir():
        with serialized_commit_path.open(mode="rb") as f:
            commit: Commit = pickle.load(f)
        if commit.message == message:
            filtered_list.append(commit.hash)
    if filtered_list == []:
        raise PyGitletException("Found no commit with that message.")
    return "\n".join(filtered_list)

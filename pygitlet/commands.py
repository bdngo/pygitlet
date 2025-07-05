import hashlib
import pickle
from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Self

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
    name: str
    contents: str
    diff: Diff

    @property
    def hash(self) -> str:
        return hashlib.sha1(self.contents.encode(encoding="ascii")).hexdigest()


@dataclass(frozen=True)
class Commit:
    timestamp: datetime
    message: str
    parent: Self | tuple[Self, Self] | None
    file_blob_map: frozendict[Path, Blob] = field(default_factory=frozendict)


def write_commit(repo: Repository, commit: Commit) -> None:
    commit_hash = hash(commit)
    with (repo.commits / f"{commit_hash:x}").open(mode="wb") as f:
        pickle.dump(commit, f)


@dataclass
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
) -> Branch:
    branch.commit = commit
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

    Path.mkdir(repo.gitlet)
    Path.mkdir(repo.commits)
    Path.mkdir(repo.blobs)
    Path.mkdir(repo.stage)
    Path.mkdir(repo.branches)

    init_commit = Commit(datetime.min, "initial commit", None)
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
        file_path.name,
        contents,
        (
            Diff.CHANGED
            if file_path.name in current_branch.commit.file_blob_map
            else Diff.ADDED
        ),
    )
    with (repo.stage / file_path.name).open(mode="wb") as f:
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
        datetime.now(),
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
        or file_path.name in current_branch.commit.file_blob_map
    ):
        raise PyGitletException("No reason to remove the file.")

    with file_path.open() as f:
        contents = f.read()
    current_branch = get_current_branch(repo)

    blob = Blob(file_path.name, contents, Diff.REMOVED)
    with (repo.stage / file_path.name).open(mode="wb") as f:
        pickle.dump(blob, f)

    file_path.unlink()

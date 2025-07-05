import hashlib
import pickle
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
        return self.gitlet / ".current-branch"


@dataclass(frozen=True)
class Blob:
    name: str
    contents: str
    hash: str


def file_hash(path: Path) -> str:
    with path.open(encoding="ascii") as f:
        contents = f.read()
        print(contents)
        return hashlib.sha1(contents).hexdigest()


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
            A Gitlet version-control system
            already exists in the current directory.
            """
            )
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


def add(repo: Repository, file: str) -> None:
    file_path = Path(file)
    if not file_path.exists():
        raise PyGitletException("File does not exist.")

    with file_path.open() as f:
        contents = f.read()
    blob = Blob(file_path.name, contents, file_hash(file_path))
    with (repo.stage / file_path.name).open(mode="wb") as f:
        pickle.dump(blob, f)


def commit(repo: Repository, message: str) -> None:
    if list(repo.stage.iterdir()) == []:
        print("No changes added to the commit.")
    elif message == "":
        raise PyGitletException("Please enter a commit message.")

    blob_dict = {}
    for k in repo.stage.iterdir():
        with k.open(mode="rb") as f:
            blob: Blob = pickle.load(f)
        if not (repo.blobs / blob.hash).exists():
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

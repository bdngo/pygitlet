import dataclasses
import hashlib
import pickle
from enum import StrEnum, auto
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from textwrap import dedent
from typing import NamedTuple, Self

from frozendict import frozendict

from .errors import PyGitletException


@dataclass(frozen=True, slots=True)
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


class Diff(StrEnum):
    ADDED = auto()
    DELETED = auto()
    MODIFIED = auto()


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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
    absolute_path = repo.gitlet.parent / file_path
    if not absolute_path.exists():
        raise PyGitletException("File does not exist.")

    with absolute_path.open() as f:
        contents = f.read()
    current_commit = get_current_branch(repo).commit

    blob = Blob(
        file_path,
        contents,
        (Diff.MODIFIED if file_path in current_commit.file_blob_map else Diff.ADDED),
    )
    stage_file_path = repo.stage / file_path
    if (
        file_path in current_commit.file_blob_map
        and current_commit.file_blob_map[file_path] == blob.hash
    ):
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
        if not (repo.blobs / blob.hash).exists() or blob.diff != Diff.DELETED:
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
    stage_file_path = repo.stage / file_path

    if (
        not stage_file_path.exists()
        or file_path not in current_branch.commit.file_blob_map
    ):
        raise PyGitletException("No reason to remove the file.")

    stage_file_path.unlink(missing_ok=True)

    absolute_path = repo.gitlet.parent / file_path
    with absolute_path.open() as f:
        contents = f.read()
    current_branch = get_current_branch(repo)

    blob = Blob(file_path, contents, Diff.DELETED)
    with stage_file_path.open(mode="wb") as f:
        pickle.dump(blob, f)

    absolute_path.unlink()


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
        current_commit = (
            current_commit.parent.origin
            if current_commit.is_merge_commit
            else current_commit.parent
        )
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


def branch_status(repo: Repository) -> str:
    branch_list = []
    for branch_path in repo.branches.iterdir():
        if not branch_path.is_symlink():
            with branch_path.open(mode="rb") as f:
                branch: Branch = pickle.load(f)
            branch_list.append(branch)
    sorted_branch_list = sorted(branch_list, key=lambda x: x.name)
    branch_string = "\n".join(
        f"*{b.name}" if b.is_current else b.name for b in sorted_branch_list
    )
    if branch_string != "":
        branch_string += "\n"
    return branch_string


def stage_status(repo: Repository) -> tuple[str, str]:
    staged_blobs = []
    for blob_path in repo.stage.iterdir():
        with blob_path.open(mode="rb") as f:
            blob: Blob = pickle.load(f)
        staged_blobs.append(blob)
    staged_files = "\n".join(
        sorted(str(b.name) for b in staged_blobs if b.diff != Diff.DELETED)
    )
    removed_files = "\n".join(
        sorted(str(b.name) for b in staged_blobs if b.diff == Diff.DELETED)
    )
    if staged_files != "":
        staged_files += "\n"
    if removed_files != "":
        removed_files += "\n"
    return staged_files, removed_files


def modified_status(repo: Repository) -> str:
    staged_blobs = []
    for blob_path in repo.stage.iterdir():
        with blob_path.open(mode="rb") as f:
            blob: Blob = pickle.load(f)
        staged_blobs.append(blob)

    modified_files_with_diff = {}
    current_commit = get_current_branch(repo).commit
    for relative_path, blob_hash in current_commit.file_blob_map.items():
        if (repo.gitlet.parent / relative_path).exists():
            with (repo.gitlet.parent / relative_path).open() as f:
                contents = f.read()
            if hashlib.sha1(contents.encode(encoding="utf-8")).hexdigest() != blob_hash:
                modified_files_with_diff[relative_path] = Diff.MODIFIED
        else:
            potentially_staged_for_removal = repo.stage / relative_path
            if not potentially_staged_for_removal.exists():
                modified_files_with_diff[relative_path] = Diff.DELETED
    for staged_blob in staged_blobs:
        if staged_blob.diff == Diff.ADDED:
            if (repo.gitlet.parent / staged_blob.name).exists():
                with (repo.gitlet.parent / staged_blob.name).open() as f:
                    contents = f.read()
                if (
                    hashlib.sha1(contents.encode(encoding="utf-8")).hexdigest()
                    != staged_blob.hash
                ):
                    modified_files_with_diff[staged_blob.name] = Diff.MODIFIED
            else:
                modified_files_with_diff[staged_blob.name] = Diff.DELETED
    modified_files = "\n".join(
        f"{path} ({tag.value})"
        for path, tag in sorted(modified_files_with_diff.items())
    )
    if modified_files != "":
        modified_files += "\n"
    return modified_files


def untracked_status(repo: Repository) -> str:
    current_commit = get_current_branch(repo).commit
    untracked_files = "\n".join(
        f.name
        for f in repo.gitlet.parent.iterdir()
        if f.is_file()
        and not (repo.stage / f.relative_to(repo.gitlet.parent)).exists()
        and f.relative_to(repo.gitlet.parent) not in current_commit.file_blob_map
    )
    if untracked_files != "":
        untracked_files += "\n"
    return untracked_files


def status(repo: Repository) -> str:
    branch_string = branch_status(repo)
    staged_files, removed_files = stage_status(repo)
    modified_files = modified_status(repo)
    untracked_files = untracked_status(repo)

    return dedent(
        f"""
    === Branches ===
    {branch_string}
    === Staged Files ===
    {staged_files}
    === Removed Files ===
    {removed_files}
    === Modifications Not Staged For Commit ===
    {modified_files}
    === Untracked Files ===
    {untracked_files}"""
    ).strip()

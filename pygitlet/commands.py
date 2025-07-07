"""Commands for PyGitlet."""

import dataclasses
import hashlib
import pickle
import queue
from enum import StrEnum, auto
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from textwrap import dedent
from typing import Generator, Self

from frozendict import frozendict

from .errors import PyGitletException


@dataclass(frozen=True, slots=True)
class Repository:
    """
    Dataclass for holding all PyGitlet folders.
    """

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
    """
    Enum for file diff types.
    """

    ADDED = auto()
    DELETED = auto()
    MODIFIED = auto()


def hash_contents(contents: str) -> str:
    """Returns SHA-1 hash of a string."""
    return hashlib.sha1(contents.encode(encoding="utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Blob:
    """
    Dataclass for file blobs.
    """

    name: Path
    contents: str
    diff: Diff

    @property
    def hash(self) -> str:
        """Returns SHA-1 hash of file contents."""
        return hash_contents(self.contents)


@dataclass(frozen=True, slots=True)
class Commit:
    """
    Dataclass for commits, with mapping
    from relative file paths to blobs.
    """

    timestamp: datetime
    message: str
    parents: list[Self] = field(default_factory=list)
    file_blob_map: frozendict[Path, Blob] = field(default_factory=frozendict)

    @property
    def hash(self) -> str:
        """Returns SHA-1 hash of serialized commit."""
        commit_serialized = pickle.dumps(self)
        return hashlib.sha1(commit_serialized).hexdigest()

    @property
    def is_merge_commit(self) -> bool:
        return len(self.parents) > 1


def write_commit(repo: Repository, commit: Commit) -> None:
    """
    Writes a commit to the repository commit folder.

    Args:
        repo: PyGitlet repository.
        commit: Commit to serialize and save.
    """
    with (repo.commits / commit.hash).open(mode="wb") as f:
        pickle.dump(commit, f)


@dataclass(frozen=True, slots=True)
class Branch:
    """
    Dataclass for a branch, i.e. a pointer to a commit.
    """

    name: str
    commit: Commit
    is_current: bool


def write_branch(repo: Repository, branch: Branch) -> None:
    """
    Serializes and saves a branch to the repository branch folder,
    and reassigns the current branch symlink if necessary.

    Args:
        repo: PyGitlet repository.
        branch: Branch to serialize and save.
    """

    with (repo.branches / branch.name).open(mode="wb") as f:
        pickle.dump(branch, f)
    if branch.is_current:
        repo.current_branch.unlink(missing_ok=True)
        repo.current_branch.symlink_to(repo.branches / branch.name)


def get_current_branch(repo: Repository) -> Branch:
    """
    Utility function to get the current branch
    based on the branch folder symlink.

    Args:
        repo: PyGitlet repository.

    Returns:
        The current working branch.
    """
    with repo.current_branch.open(mode="rb") as f:
        return pickle.load(f)


def set_branch_commit(
    repo: Repository,
    branch: Branch,
    commit: Commit,
) -> None:
    """
    Moves a branch to a given commit.

    Args:
        repo: PyGitlet repository.
        branch: Branch to move.
        commit: Commit to assign to branch.
    """
    branch = dataclasses.replace(branch, commit=commit)
    write_branch(repo, branch)


def init(repo: Repository) -> None:
    """
    Initalizes a new PyGitlet repository in the given repository.

    Args:
        repo: Folder to create a PyGitlet repository in.

    Raises:
        PyGitletException: If a PyGitlet repository already exists in the intended repository.
    """
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
    init_commit = Commit(aware_unix_epoch, "initial commit")
    init_branch = Branch("main", init_commit, True)

    write_branch(repo, init_branch)
    write_commit(repo, init_commit)


def add(repo: Repository, file_path: Path) -> None:
    """
    Stages a file. Overwrites existing staged files
    if the same named file exists and differs.
    If identical, the file is unstaged.
    If previously staged for removal, undoes this.

    Args:
        repo: PyGitlet repository.
        file_path: Relative path to file to be staged.

    Raises:
        PyGitletException: If the requested file doesn't exist.
    """
    stage_file_path = repo.stage / file_path
    if stage_file_path.exists():
        with stage_file_path.open(mode="rb") as f:
            potentially_staged_for_removal: Blob = pickle.load(f)
        if potentially_staged_for_removal.diff == Diff.DELETED:
            potentially_staged_for_removal = dataclasses.replace(
                potentially_staged_for_removal, diff=Diff.ADDED
            )
        with stage_file_path.open(mode="wb") as f:
            pickle.dump(potentially_staged_for_removal, f)
        return

    absolute_path = repo.gitlet.parent / file_path
    if not absolute_path.exists():
        raise PyGitletException("File does not exist.")

    contents = absolute_path.read_text()
    current_commit = get_current_branch(repo).commit

    blob = Blob(
        file_path,
        contents,
        (Diff.MODIFIED if file_path in current_commit.file_blob_map else Diff.ADDED),
    )
    if (
        file_path in current_commit.file_blob_map
        and current_commit.file_blob_map[file_path].hash == blob.hash
    ):
        stage_file_path.unlink(missing_ok=True)
    else:
        with stage_file_path.open(mode="wb") as f:
            pickle.dump(blob, f)


def commit(repo: Repository, message: str) -> None:
    """
    Commits changes to the repository.

    Args:
        repo: PyGitlet repository.
        message: Commit message.

    Raises:
        PyGitletException: If the stage is empty or there is no commit message.
    """
    if list(repo.stage.iterdir()) == []:
        raise PyGitletException("No changes added to the commit.")
    elif message == "":
        raise PyGitletException("Please enter a commit message.")

    current_branch = get_current_branch(repo)
    blob_dict = current_branch.commit.file_blob_map
    for k in repo.stage.iterdir():
        with k.open(mode="rb") as f:
            blob: Blob = pickle.load(f)
        if not (repo.blobs / blob.hash).exists() or blob.diff != Diff.DELETED:
            with (repo.blobs / blob.hash).open(mode="wb") as f:
                pickle.dump(blob, f)
        blob_dict = blob_dict.set(blob.name, blob)
        k.unlink()

    commit = Commit(
        datetime.now().astimezone(),
        message,
        [current_branch.commit],
        file_blob_map=blob_dict,
    )
    write_commit(repo, commit)

    set_branch_commit(repo, current_branch, commit)


def remove(repo: Repository, file_path: Path) -> None:
    """
    Stages a file for removal.
    Note that this is different from manually removing a file.

    Args:
        repo: PyGitlet repository.
        file_path: Relative path to the file being removed.

    Raises:
        PyGitletException: If the file either doesn't exist or is not tracked by the current commit.
    """
    current_branch = get_current_branch(repo)
    stage_file_path = repo.stage / file_path

    if (
        not stage_file_path.exists()
        and file_path not in current_branch.commit.file_blob_map
    ):
        raise PyGitletException("No reason to remove the file.")

    stage_file_path.unlink(missing_ok=True)

    absolute_path = repo.gitlet.parent / file_path
    contents = absolute_path.read_text()
    current_branch = get_current_branch(repo)

    blob = Blob(file_path, contents, Diff.DELETED)
    with stage_file_path.open(mode="wb") as f:
        pickle.dump(blob, f)

    absolute_path.unlink()


def format_commit(commit: Commit) -> str:
    """
    Utility function to format commits for logging.
    Merge commits have the origin branch first, then the target branch.
    Timestamps are displayed with the local UTC offset.

    Args:
        commit: Commit to format.

    Returns:
        A string for logging.
    """
    timestamp_formatted = commit.timestamp.strftime("%a %b %-d %X %Y %z")
    if commit.is_merge_commit:
        message = f"===\ncommit {commit.hash}\nMerge: {commit.parents[0].hash[:7]} {commit.parents[1].hash[:7]}\nDate: {timestamp_formatted}\n{commit.message}\n\n"
    else:
        message = f"===\ncommit {commit.hash}\nDate: {timestamp_formatted}\n{commit.message}\n\n"
    return message


def log(repo: Repository) -> str:
    """
    Displays a log of the current linear commit history.
    This means it does not show commit history that the working branch does not share.

    Args:
        repo: PyGitlet repository.

    Returns:
        Linear history log to print.
    """
    current_commit = get_current_branch(repo).commit
    log = StringIO()
    while True:
        log.write(format_commit(current_commit))
        if current_commit.parents != []:
            current_commit = current_commit.parents[0]
        else:
            break
    log.seek(0)
    return log.read().strip()


def global_log(repo: Repository) -> str:
    """
    Displays a global log of all repository commits, regardless of working branch.

    Args:
        repo: PyGitlet repository.

    Returns:
        Global history log to print.
    """
    log = StringIO()
    for serialized_commit_path in repo.commits.iterdir():
        with serialized_commit_path.open(mode="rb") as f:
            commit: Commit = pickle.load(f)
        log.write(format_commit(commit))
    log.seek(0)
    return log.read().strip()


def find(repo: Repository, message: str) -> str:
    """
    Searches for commits with a given commit message.
    Search is exact and case-sensitive.

    Args:
        repo: PyGitlet repository.
        message: Search query.

    Returns:
        IDs of commits with matching messages.
    """
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
    """
    Utility function to generate status of branches.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted branches, with the working branch marked.
    """
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
    """
    Utility function to generate status of staged files.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted staged files split into added/modified and removed files.
    """
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
    """
    Utility function to generate status of unstaged & modified files.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted unstaged modified files with the type of diff indicated.
    """
    staged_blobs = []
    for blob_path in repo.stage.iterdir():
        with blob_path.open(mode="rb") as f:
            blob: Blob = pickle.load(f)
        staged_blobs.append(blob)

    modified_files_with_diff = {}
    current_commit = get_current_branch(repo).commit
    for relative_path, blob in current_commit.file_blob_map.items():
        if (repo.gitlet.parent / relative_path).exists():
            contents = (repo.gitlet.parent / relative_path).read_text()
            if hash_contents(contents) != blob.hash:
                modified_files_with_diff[relative_path] = Diff.MODIFIED
        else:
            potentially_staged_for_removal = repo.stage / relative_path
            if not potentially_staged_for_removal.exists():
                modified_files_with_diff[relative_path] = Diff.DELETED
    for staged_blob in staged_blobs:
        if staged_blob.diff == Diff.ADDED:
            if (repo.gitlet.parent / staged_blob.name).exists():
                contents = (repo.gitlet.parent / staged_blob.name).read_text()
                if hash_contents(contents) != staged_blob.hash:
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
    """
    Utility function to generate status of untracked files.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted untracked files, excluding subdirectories.
    """
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
    """
    Prints status of repository.

    Args:
        repo: PyGitlet repository.

    Returns:
        Status of repository, including branches, staged files, modified tracked files, and untracked files.
    """
    branch_string = branch_status(repo)
    staged_files, removed_files = stage_status(repo)
    modified_files = modified_status(repo)
    untracked_files = untracked_status(repo)

    return "\n".join(
        [
            f"=== Branches ===\n{branch_string}",
            f"=== Staged Files ===\n{staged_files}",
            f"=== Removed Files ===\n{removed_files}",
            f"=== Modifications Not Staged For Commit ===\n{modified_files}",
            f"=== Untracked Files ===\n{untracked_files}",
        ]
    ).strip()


def checkout_file(repo: Repository, file_path: Path) -> None:
    """
    Checks out a file from the head commit, overwriting the working version.

    Args:
        repo: PyGitlet repository.
        file_path: Relative path to file to check out.

    Raises:
        PyGitletException: If the file is not tracked by the current commit.
    """
    current_commit = get_current_branch(repo).commit
    if file_path not in current_commit.file_blob_map:
        raise PyGitletException("File does not exist in that commit.")
    file_blob = current_commit.file_blob_map[file_path]
    (repo.gitlet.parent / file_path).write_text(file_blob.contents)


def checkout_commit(repo: Repository, commit_id: str, file_path: Path) -> None:
    """
    Checkouts a file from a given commit ID, overwriting the working version.

    Args:
        repo: PyGitlet repository.
        commit_id: SHA-1 hash (or substring thereof) of commit to check out from.
        file_path: Relative path of file to check out.

    Raises:
        PyGitletException: If the commit ID does not exist or the file is not tracked by the desired commit.
    """
    commit_glob = repo.commits.glob(f"{commit_id}*")
    try:
        with next(commit_glob).open(mode="rb") as f:
            found_commit: Commit = pickle.load(f)
    except StopIteration:
        raise PyGitletException("No commit with that id exists.")

    if file_path not in found_commit.file_blob_map:
        raise PyGitletException("File does not exist in that commit.")
    file_blob = found_commit.file_blob_map[file_path]
    (repo.gitlet.parent / file_path).write_text(file_blob.contents)


def checkout_branch(repo: Repository, branch_name: str) -> None:
    """
    Switches branches, overwriting all tracked files in the working directory.

    Args:
        repo: PyGitlet repository.
        branch_name: Name of branch to switch to.

    Raises:
        PyGitletException: If the branch doesn't exist, is the current branch,
        or if there are any untracked files that would be overwritten by the checkout.
    """
    current_branch = get_current_branch(repo)
    if not (repo.branches / branch_name).exists():
        raise PyGitletException("No such branch exists.")
    if current_branch.name == branch_name:
        raise PyGitletException("No need to checkout the current branch.")

    with (repo.branches / branch_name).open(mode="rb") as f:
        target_branch: Branch = pickle.load(f)

    for file_name, blob in target_branch.commit.file_blob_map.items():
        absolute_path = repo.gitlet.parent / file_name
        if (
            absolute_path.exists()
            and file_name not in current_branch.commit.file_blob_map
        ):
            raise PyGitletException(
                "There is an untracked file in the way; delete it, or add and commit it first."
            )
        absolute_path.write_text(blob.contents)

    for old_file_name, blob in current_branch.commit.file_blob_map.items():
        absolute_path = repo.gitlet.parent / old_file_name
        if old_file_name not in target_branch.commit.file_blob_map:
            absolute_path.unlink()

    for staged_file in repo.stage.iterdir():
        if staged_file.is_file():
            staged_file.unlink()

    updated_current_branch = dataclasses.replace(current_branch, is_current=False)
    updated_target_branch = dataclasses.replace(target_branch, is_current=True)
    write_branch(repo, updated_current_branch)
    write_branch(repo, updated_target_branch)


def branch(repo: Repository, branch_name: str) -> None:
    """
    Creates a new branch pointing to the current commit.

    Args:
        repo: PyGitlet repository.
        branch_name: New branch name.

    Raises:
        PyGitletException: If the desired branch name already exists.
    """
    if (repo.branches / branch_name).exists():
        raise PyGitletException("A branch with that name already exists.")
    current_commit = get_current_branch(repo).commit
    new_branch = Branch(branch_name, current_commit, False)
    write_branch(repo, new_branch)


def remove_branch(repo: Repository, branch_name: str) -> None:
    """
    Deletes a branch.

    Args:
        repo: PyGitlet repository.
        branch_name: Branch to delete.

    Raises:
        PyGitletException: If the branch either doesn't exist or is the current working branch.
    """
    if not (repo.branches / branch_name).exists():
        raise PyGitletException("A branch with that name does not exist.")
    if get_current_branch(repo).name == branch_name:
        raise PyGitletException("Cannot remove the current branch.")
    (repo.branches / branch_name).unlink()


def reset(repo: Repository, commit_id: str) -> None:
    """
    Resets the working directory to a given commit ID.

    Args:
        repo: PyGitlet repository.
        commit_id: SHA-1 hash (or substring thereof) of commit.

    Raises:
        PyGitletException: If the commit ID doesn't exist or if the reset would overwrite any untracked files.
    """
    if not (repo.commits / commit_id).exists():
        raise PyGitletException("No commit with that id exists.")

    current_commit = get_current_branch(repo).commit
    with (repo.commits / commit_id).open(mode="rb") as f:
        target_commit: Commit = pickle.load(f)

    for file_name, blob in target_commit.file_blob_map.items():
        absolute_path = repo.gitlet.parent / file_name
        if absolute_path.exists() and file_name not in current_commit.file_blob_map:
            raise PyGitletException(
                "There is an untracked file in the way; delete it, or add and commit it first."
            )
        absolute_path.write_text(blob.contents)

    for old_file_name, blob in current_commit.file_blob_map.items():
        absolute_path = repo.gitlet.parent / old_file_name
        if old_file_name not in current_commit.file_blob_map:
            absolute_path.unlink()

    for staged_file in repo.stage.iterdir():
        if staged_file.is_file():
            staged_file.unlink()

    moved_branch = dataclasses.replace(get_current_branch(repo), commit=target_commit)
    write_branch(repo, moved_branch)


def commit_history(commit: Commit) -> list[Commit]:
    """
    Tracks the entire history of a commit using the topological sort of its DAG.

    Args:
        commit: Commit to start tracking history from.

    Returns:
        List of commits in ascending topological sort.
    """
    commit_bfs = []
    commit_queue: queue.SimpleQueue[Commit] = queue.SimpleQueue()
    commit_queue.put(commit)
    while not commit_queue.empty():
        current_commit = commit_queue.get()
        if current_commit not in commit_bfs:
            commit_bfs.append(current_commit)
            for parent in current_commit.parents:
                if commit_bfs not in commit_bfs:
                    commit_queue.put(parent)
    return commit_bfs


def latest_common_ancestor(repo: Repository, origin: Commit, target: Commit) -> Commit:
    """
    Finds the latest common ancestor (i.e. split point) for two commits.

    Args:
        origin: Current commit.
        target: Commit to be merged.

    Returns:
        The latest commit to which two commits share the same parent.
        If multiple commits qualify, then ties are broken by distance to current commit.
    """
    origin_history = commit_history(origin)
    target_history = commit_history(target)

    # join commits at root to find last shared commit
    reversed_joined_history = zip(reversed(origin_history), reversed(target_history))

    # keep shared commits, take last one
    lca = [c1 for (c1, c2) in reversed_joined_history if c1 == c2][-1]
    return lca


def merge(repo: Repository, target_branch_name: str) -> None:
    """
    Merges the target branch into the current branch.

    Args:
        repo: PyGitlet repository.
        target_branch: Name of branch to merge into the current branch.

    Raises:
        PyGitletException: If the stage is nonempty, target branch doesn't exist, is itself, or if there are uncommitted changes.
    """
    if len(list(repo.stage.iterdir())) != 0:
        raise PyGitletException("You have uncommitted changes.")
    if not (repo.branches / target_branch_name).exists():
        raise PyGitletException("A branch with that name does not exist.")
    if get_current_branch(repo).name == target_branch_name:
        raise PyGitletException("Cannot merge a branch with itself.")

    with (repo.branches / target_branch_name).open(mode="rb") as f:
        target_branch: Branch = pickle.load(f)
    origin_branch_commit = get_current_branch(repo).commit
    target_branch_commit = target_branch.commit
    lca = latest_common_ancestor(repo, origin_branch_commit, target_branch_commit)

    for file_name, blob in target_branch_commit.file_blob_map:
        absolute_path = repo.gitlet.parent / file_name
        if (
            absolute_path.exists()
            and file_name not in origin_branch_commit.file_blob_map
        ):
            raise PyGitletException(
                "There is an untracked file in the way; delete it, or add and commit it first."
            )

    for file_name, blob in lca.file_blob_map:
        if (
            file_name in target_branch_commit.file_blob_map
            and target_branch_commit.file_blob_map[file_name].hash != blob.hash
            and file_name in origin_branch_commit.file_blob_map
            and origin_branch_commit.file_blob_map[file_name].hash == blob.hash
        ):
            ...
    raise NotImplementedError
    # for file_name, blob in target_branch_commit.file_blob_map:
    #     if file_name not in lca.file_blob_map:

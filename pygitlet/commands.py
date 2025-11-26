"""Commands for PyGitlet."""

import dataclasses
import hashlib
import pickle
import queue
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum, auto
from io import StringIO
from pathlib import Path
from textwrap import dedent

import sqlalchemy as sa
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    Session,
    mapped_column,
    relationship,
)

from pygitlet.errors import PyGitletException


@dataclass(frozen=True, slots=True)
class Repository:
    """Root class for Gitlet repo."""

    gitlet: Path


class Base(DeclarativeBase, MappedAsDataclass):
    pass


class Diff(StrEnum):
    """Enum for file diff types."""

    ADDED = auto()
    DELETED = auto()
    MODIFIED = auto()


def hash_contents(contents: str) -> str:
    """Returns SHA-1 hash of a string.

    Args:
        contents: String contents to hash.

    Returns:
        SHA-1 hash of string after UTF-8 encoding.
    """
    return hashlib.sha1(contents.encode(encoding="utf-8")).hexdigest()


blob_to_commit = sa.Table(
    "blob_to_commit",
    Base.metadata,
    sa.Column("blob_id", sa.Integer, sa.ForeignKey("blob.id"), primary_key=True),
    sa.Column("commit_id", sa.Integer, sa.ForeignKey("commit.id"), primary_key=True),
)


class Blob(Base):
    """Dataclass for file blobs."""

    __tablename__ = "blob"
    __table_args__ = (sa.UniqueConstraint("name", "contents"),)

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[str]
    contents: Mapped[str]
    diff: Mapped[Diff]
    staged: Mapped[bool]
    commit: Mapped[list["Commit"]] = relationship(
        init=False, secondary=blob_to_commit, back_populates="file_blob_map"
    )

    @property
    def hash(self) -> str:
        """Returns SHA-1 hash of file contents."""
        return hash_contents(self.contents)


commit_to_parent = sa.Table(
    "commit_to_parent",
    Base.metadata,
    sa.Column("commit_id", sa.Integer, sa.ForeignKey("commit.id"), primary_key=True),
    sa.Column("parent_id", sa.Integer, sa.ForeignKey("commit.id"), primary_key=True),
)


class Commit(Base):
    """
    Dataclass for commits, with mapping
    from relative file paths to blobs.
    """

    __tablename__ = "commit"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    timestamp: Mapped[datetime]
    message: Mapped[str]
    parents: Mapped[list["Commit"]] = relationship(
        secondary=commit_to_parent,
        primaryjoin=id == commit_to_parent.c.commit_id,
        secondaryjoin=id == commit_to_parent.c.parent_id,
        order_by="Commit.id",
    )
    file_blob_map: Mapped[list[Blob]] = relationship(
        secondary=blob_to_commit, back_populates="commit", order_by="Blob.name"
    )

    @property
    def hash(self) -> str:
        """Returns SHA-1 hash of serialized commit.

        Returns:
            SHA-1 hash of commit object.
        """
        data = (
            self.message,
            self.timestamp,
            [p.hash for p in self.parents],
            [(b.name, b.hash) for b in self.file_blob_map],
        )
        commit_serialized = pickle.dumps(data)
        return hashlib.sha1(commit_serialized).hexdigest()

    @property
    def is_merge_commit(self) -> bool:
        return len(self.parents) > 1


class Branch(Base):
    """Dataclass for a branch, i.e. a pointer to a commit."""

    __tablename__ = "branch"

    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    local_name: Mapped[str]
    commit: Mapped[Commit] = relationship()
    commit_id: Mapped[int] = mapped_column(sa.ForeignKey("commit.id"), init=False)
    is_current: Mapped[bool]
    remote: Mapped[str] = mapped_column(default=None, nullable=True)

    @property
    def name(self) -> str:
        """Get name of branch, accounting for remote branches."""
        return (
            self.local_name
            if self.remote is None
            else f"{self.remote}/{self.local_name}"
        )


def get_current_branch(db: Session) -> Branch:
    """Utility function to get the current branch.

    Args:
        db: Database session.

    Returns:
        The current working branch.
    """
    return db.execute(sa.select(Branch).filter_by(is_current=True)).scalar_one()


def set_branch_commit(
    db: Session,
    branch: Branch,
    commit: Commit,
) -> None:
    """Moves a branch to a given commit.

    Args:
        db: Database session.
        branch: Branch to move.
        commit: Commit to assign to branch.
    """
    branch.commit_id = commit.id


def init(repo: Repository) -> None:
    """Initalizes a new PyGitlet repository in the given repository.

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
    db_path = repo.gitlet / "db.sqlite3"
    db_path.touch()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")

    aware_unix_epoch = datetime.fromtimestamp(0, tz=timezone.utc).astimezone()
    init_commit = Commit(aware_unix_epoch, "initial commit", [], [])
    init_branch = Branch("main", init_commit, True)

    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        session.add(init_branch)
        session.add(init_commit)
        session.commit()


def add(repo: Repository, db: Session, file_path: Path) -> None:
    """Stages a file. Overwrites existing staged files
    if the same named file exists and differs.
    If identical, the file is unstaged.
    If previously staged for removal, undoes this.

    Args:
        repo: PyGitlet repository.
        db: Database session.
        file_path: Relative path to file to be staged.

    Raises:
        PyGitletException: If the requested file doesn't exist.
    """
    staged_blob = db.execute(
        sa.select(Blob).filter_by(name=str(file_path), staged=True)
    ).scalar_one_or_none()
    if staged_blob is not None:
        if staged_blob.diff == Diff.DELETED:
            staged_blob.diff = Diff.ADDED
            db.commit()
        return

    absolute_path = repo.gitlet.parent / file_path
    if not absolute_path.exists():
        raise PyGitletException("File does not exist.")

    contents = absolute_path.read_text()
    current_commit = get_current_branch(db).commit

    current_commit_blob_hashes = {
        blob.name: blob.hash for blob in current_commit.file_blob_map
    }
    if str(file_path) in current_commit_blob_hashes and current_commit_blob_hashes[
        str(file_path)
    ] == hash_contents(contents):
        return

    blob = Blob(
        name=str(file_path),
        contents=contents,
        diff=(
            Diff.MODIFIED
            if str(file_path) in current_commit_blob_hashes
            else Diff.ADDED
        ),
        staged=True,
    )
    db.merge(blob)
    db.commit()


def commit(repo: Repository, db: Session, message: str) -> None:
    """Commits changes to the repository.

    Args:
        repo: PyGitlet repository.
        db: Database session.
        message: Commit message.

    Raises:
        PyGitletException: If the stage is empty or there is no commit message.
    """
    all_blobs = sa.select(Blob)
    staged_blobs = all_blobs.filter_by(staged=True)
    if len(db.execute(staged_blobs).all()) == 0:
        raise PyGitletException("No changes added to the commit.")
    elif message == "":
        raise PyGitletException("Please enter a commit message.")

    current_branch = get_current_branch(db)
    current_commit_tracked = current_branch.commit.file_blob_map
    new_commit_tracked: list[Blob] = []
    for blob in db.execute(staged_blobs).scalars():
        if blob.diff == Diff.DELETED:
            db.execute(sa.delete(Blob).filter_by(id=blob.id))
        else:
            current_commit_tracked_names = [
                blob.name for blob in current_commit_tracked
            ]
            if (
                db.execute(
                    all_blobs.where(blob.name in current_commit_tracked_names)
                ).first()
                is not None
            ):
                blob.diff = Diff.MODIFIED
            else:
                blob.diff = Diff.ADDED
            new_commit_tracked.append(blob)
            blob.staged = False
        db.commit()

    commit = Commit(
        datetime.now().astimezone(),
        message,
        [current_branch.commit],
        new_commit_tracked,
    )
    db.add(commit)
    db.commit()

    current_branch.commit_id = commit.id
    db.commit()


def remove(repo: Repository, db: Session, file_path: Path) -> None:
    """
    Stages a file for removal.
    Note that this is different from manually removing a file.

    Args:
        repo: PyGitlet repository.
        db: Database session.
        file_path: Relative path to the file being removed.

    Raises:
        PyGitletException: If the file either doesn't exist or is not tracked by the current commit.
    """
    current_branch = get_current_branch(db)
    current_commit_tracked_map = {
        blob.name: blob for blob in current_branch.commit.file_blob_map
    }
    staged_blob = db.execute(
        sa.select(Blob).filter_by(staged=True, name=str(file_path))
    ).scalar_one_or_none()

    if staged_blob is None and str(file_path) not in current_commit_tracked_map:
        raise PyGitletException("No reason to remove the file.")

    if staged_blob:
        if staged_blob.diff == Diff.ADDED:
            db.delete(staged_blob)
        else:
            staged_blob.diff = Diff.DELETED
    else:
        blob = Blob(
            name=str(file_path),
            contents="",
            diff=Diff.DELETED,
            staged=True,
        )
        db.add(blob)

    db.commit()

    (repo.gitlet.parent / file_path).unlink(missing_ok=True)


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


def log(repo: Repository, db: Session) -> str:
    """
    Displays a log of the current linear commit history.
    This means it does not show commit history that the working branch does not share.

    Args:
        repo: PyGitlet repository.

    Returns:
        Linear history log to print.
    """
    current_commit = get_current_branch(db).commit
    log = StringIO()
    while True:
        log.write(format_commit(current_commit))
        if current_commit.parents != []:
            current_commit = current_commit.parents[0]
        else:
            break
    log.seek(0)
    return log.read().strip()


def global_log(repo: Repository, db: Session) -> str:
    """
    Displays a global log of all repository commits, regardless of working branch.

    Args:
        repo: PyGitlet repository.

    Returns:
        Global history log to print.
    """
    log = StringIO()
    all_commits = db.execute(sa.select(Commit)).scalars()
    for commit in all_commits:
        log.write(format_commit(commit))
    log.seek(0)
    return log.read().strip()


def find(repo: Repository, db: Session, message: str) -> str:
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
    all_commits = db.execute(sa.select(Commit)).scalars()
    for commit in all_commits:
        if commit.message == message:
            filtered_list.append(commit.hash)
    if filtered_list == []:
        raise PyGitletException("Found no commit with that message.")
    return "\n".join(filtered_list)


def branch_status(repo: Repository, db: Session) -> str:
    """
    Utility function to generate status of branches.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted branches, with the working branch marked.
    """
    branch_list = db.execute(sa.select(Branch).order_by(Branch.local_name)).scalars()
    branch_string = "\n".join(
        f"*{b.local_name}" if b.is_current else str(b.local_name) for b in branch_list
    )
    if branch_string != "":
        branch_string += "\n"
    return branch_string


def stage_status(repo: Repository, db: Session) -> tuple[str, str]:
    """
    Utility function to generate status of staged files.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted staged files split into added/modified and removed files.
    """
    staged_blobs = db.execute(sa.select(Blob).filter_by(staged=True)).scalars().all()
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


def modified_status(repo: Repository, db: Session) -> str:
    """
    Utility function to generate status of unstaged & modified files.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted unstaged modified files with the type of diff indicated.
    """
    staged_blobs = db.execute(sa.select(Blob).filter_by(staged=True)).scalars()
    modified_files_with_diff = {}
    current_commit = get_current_branch(db).commit
    for blob in current_commit.file_blob_map:
        if (repo.gitlet.parent / blob.name).exists():
            contents = (repo.gitlet.parent / blob.name).read_text()
            hashed_contents = hash_contents(contents)
            if (
                hashed_contents != blob.hash
                and db.execute(
                    sa.select(Blob).filter_by(staged=True, name=blob.name)
                ).scalar_one_or_none()
                is None
            ):
                modified_files_with_diff[blob.name] = Diff.MODIFIED
        else:
            if (
                db.execute(
                    sa.select(Blob).filter_by(staged=True, name=blob.name)
                ).scalar_one_or_none()
                is None
            ):
                modified_files_with_diff[blob.name] = Diff.DELETED
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


def untracked_status(repo: Repository, db: Session) -> str:
    """
    Utility function to generate status of untracked files.

    Args:
        repo: PyGitlet repository.

    Returns:
        Lexicographically sorted untracked files, excluding subdirectories.
    """
    current_commit = get_current_branch(db).commit
    current_commit_tracked_map = {b.name for b in current_commit.file_blob_map}
    untracked_files = "\n".join(
        f.name
        for f in repo.gitlet.parent.iterdir()
        if f.is_file()
        and db.execute(
            sa.select(Blob).filter_by(
                staged=True, name=str(f.relative_to(repo.gitlet.parent))
            )
        ).scalar_one_or_none()
        is None
        and str(f.relative_to(repo.gitlet.parent))
        not in current_commit_tracked_map
    )
    if untracked_files != "":
        untracked_files += "\n"
    return untracked_files


def status(repo: Repository, db: Session) -> str:
    """
    Prints status of repository.

    Args:
        repo: PyGitlet repository.

    Returns:
        Status of repository, including branches, staged files, modified tracked files, and untracked files.
    """
    branch_string = branch_status(repo, db)
    staged_files, removed_files = stage_status(repo, db)
    modified_files = modified_status(repo, db)
    untracked_files = untracked_status(repo, db)

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
        found_commit: Commit = read_object(next(commit_glob))
    except StopIteration as e:
        raise PyGitletException("No commit with that id exists.") from e

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

    target_branch: Branch = read_object(repo.branches / branch_name)

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
    target_commit: Commit = read_object(repo.commits / commit_id)

    for file_name, blob in target_commit.file_blob_map.items():
        absolute_path = repo.gitlet.parent / file_name
        if absolute_path.exists() and file_name not in current_commit.file_blob_map:
            raise PyGitletException(
                "There is an untracked file in the way; delete it, or add and commit it first."
            )
        absolute_path.write_text(blob.contents)

    for old_file_name, blob in current_commit.file_blob_map.items():
        absolute_path = repo.gitlet.parent / old_file_name
        if old_file_name not in target_commit.file_blob_map:
            absolute_path.unlink()

    for staged_file in repo.stage.iterdir():
        if staged_file.is_file():
            staged_file.unlink()

    moved_branch = dataclasses.replace(get_current_branch(repo), commit=target_commit)
    write_branch(repo, moved_branch)


def commit_history(commit: Commit) -> list[Commit]:
    """
    Tracks the entire history of a commit using BFS.

    Args:
        commit: Commit to start tracking history from.

    Returns:
        List of commits in ascending topological sort with partial order of timestamp.
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


def latest_common_ancestor(origin: Commit, target: Commit) -> Commit:
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


def generate_conflict(origin: str, target: str) -> str:
    """
    Creates a version of the a file with conflicts.

    Args:
        origin: Contents of file in the current branch.
        target: Contents of file in the given branch.

    Returns:
        New file with conflicts shown.
    """
    return f"<<<<<<< HEAD\n{origin}=======\n{target}>>>>>>>\n"


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

    target_branch: Branch = read_object(repo.branches / target_branch_name)
    origin_branch_commit = get_current_branch(repo).commit
    target_branch_commit = target_branch.commit
    lca = latest_common_ancestor(origin_branch_commit, target_branch_commit)
    if lca == target_branch_commit:
        print("Given branch is an ancestor of the current branch.")
        return
    if lca == origin_branch_commit:
        checkout_branch(repo, target_branch.name)
        print("Current branch fast-forwarded.")
        return

    conflicted = False
    origin_blob_map = origin_branch_commit.file_blob_map
    target_blob_map = target_branch_commit.file_blob_map
    for file_name, blob in target_blob_map.items():
        absolute_path = repo.gitlet.parent / file_name
        if (
            absolute_path.exists()
            and file_name not in origin_branch_commit.file_blob_map
        ):
            raise PyGitletException(
                "There is an untracked file in the way; delete it, or add and commit it first."
            )

        if file_name not in lca.file_blob_map:
            if (
                file_name in origin_blob_map
                and origin_blob_map[file_name].hash != blob.hash
            ):  # not in LCA, modified in both branches
                conflicted = True
                absolute_path.write_text(
                    generate_conflict(
                        origin_blob_map[file_name].contents, blob.contents
                    )
                )
                add(repo, file_name)
            else:  # not in LCA, unchanged in current branch, added in given branch
                checkout_commit(repo, target_branch_commit.hash, file_name)
                add(repo, file_name)
        else:
            if (
                lca.file_blob_map[file_name].hash != blob.hash
                and file_name not in origin_blob_map
            ):  # in LCA, deleted in current branch, modified in given branch
                conflicted = True
                absolute_path.write_text(generate_conflict("\n", blob.contents))
                add(repo, file_name)
            elif (
                lca.file_blob_map[file_name].hash != blob.hash
                and file_name in origin_blob_map
                and lca.file_blob_map[file_name] != origin_blob_map[file_name]
                and origin_blob_map[file_name] != blob.hash
            ):  # in LCA, modified in both branches
                conflicted = True
                absolute_path.write_text(
                    generate_conflict(
                        origin_blob_map[file_name].contents, blob.contents
                    )
                )
                add(repo, file_name)

    for file_name, blob in origin_blob_map.items():
        absolute_path = repo.gitlet.parent / file_name
        if file_name in lca.file_blob_map:
            if (
                lca.file_blob_map[file_name].hash != blob.hash
                and file_name not in target_blob_map
            ):  # in LCA, modified in current branch, deleted in given branch
                conflicted = True
                absolute_path.write_text(generate_conflict(blob.contents, "\n"))
                add(repo, file_name)
            elif (
                file_name in target_blob_map
                and lca.file_blob_map[file_name].hash == blob.hash
                and lca.file_blob_map[file_name].hash != target_blob_map[file_name].hash
            ):  # in LCA, unchanged in current branch, modified in given branch
                checkout_commit(repo, target_branch_commit.hash, file_name)
                add(repo, file_name)
            elif (
                lca.file_blob_map[file_name].hash == blob.hash
                and file_name not in target_blob_map
            ):  # in LCA, unchanged in current branch, deleted in given branch
                remove(repo, file_name)

    merge_commit(repo, get_current_branch(repo), target_branch)

    if conflicted:
        print("Encountered a merge conflict.")


def merge_commit(
    repo: Repository, origin_branch: Branch, target_branch: Branch
) -> None:
    """
    Commits merge changes to the repository.

    Args:
        repo: PyGitlet repository.
        origin_branch: Origin branch to merge into.
        target_branch: Branch to be merged.

    Raises:
        PyGitletException: If the stage is empty.
    """
    blob_dict = origin_branch.commit.file_blob_map
    for k in repo.stage.iterdir():
        blob: Blob = read_object(k)
        if not (repo.blobs / blob.hash).exists() or blob.diff != Diff.DELETED:
            write_object(repo.blobs / blob.hash, blob)
        if blob.diff != Diff.DELETED:
            blob_dict = blob_dict.set(blob.name, blob)
        else:
            blob_dict = blob_dict.delete(blob.name)
        k.unlink()

    commit = Commit(
        datetime.now().astimezone(),
        f"Merged {target_branch.name} into {origin_branch.name}",
        [origin_branch.commit, target_branch.commit],
        file_blob_map=blob_dict,
    )
    write_object(repo.commits / commit.hash, commit)

    set_branch_commit(repo, origin_branch, commit)


def add_remote(repo: Repository, remote_name: str, remote_path: Repository) -> None:
    """
    Adds a remote to the remote directory.

    Args:
        repo: PyGitlet repository.
        remote_name: Name of remote.
        remote_path: Path to remote .gitlet folder.

    Raises:
        PyGitletException: If the remote name already exists.
    """
    remote_folder = repo.branches / remote_name
    if remote_folder.exists() and remote_folder.is_dir():
        raise PyGitletException("A remote with that name already exists.")

    remote_folder.mkdir()
    (remote_folder / ".gitlet").symlink_to(remote_path.gitlet)


def remove_remote(repo: Repository, remote_name: str) -> None:
    """
    Removes a remote from the remote directory.

    Args:
        repo: PyGitlet repository.
        remote_name: Name of remote.

    Raises:
        PyGitletException: If the remote name does not exist.
    """
    remote_folder = repo.branches / remote_name
    if not remote_folder.exists():
        raise PyGitletException("A remote with that name does not exist.")

    for f in remote_folder.iterdir():
        f.unlink()
    remote_folder.rmdir()


def push(repo: Repository, remote_name: str, remote_branch_name: str) -> None:
    """
    Pushes a local branch to a remote. Creates the branch on the remote if it doesn't exist.

    Args:
        repo: PyGitlet repository.
        remote_name: Name of the remote on the local repo.
        remote_branch_name: Name of the branch on the remote repo.

    Raises:
        PyGitletException: If the remote doesn't exist or the branch on the remote doesn't exist.
    """
    remote_folder = repo.branches / remote_name
    remote_gitlet = (remote_folder / ".gitlet").resolve()
    if not remote_gitlet.exists():
        raise PyGitletException("Remote directory not found.")
    repo_remote = Repository(remote_gitlet)

    if not (repo_remote.branches / remote_branch_name).exists():
        new_branch_to_remote = dataclasses.replace(
            get_current_branch(repo), is_current=False
        )
        write_branch(repo_remote, new_branch_to_remote)
        return

    remote_branch: Branch = read_object(remote_folder / remote_branch_name)
    current_commit = get_current_branch(repo).commit
    current_commit_history = commit_history(current_commit)
    current_commit_history.reverse()
    if remote_branch.commit not in current_commit_history:
        raise PyGitletException("Please pull down remote changes before pushing.")

    future_commit_index = current_commit_history.index(remote_branch.commit)
    commits_to_push = current_commit_history[future_commit_index + 1 :].copy()
    for commit in commits_to_push:
        write_object(repo_remote.commits / commit.hash, commit)
    reset(repo_remote, current_commit.hash)


def fetch(repo: Repository, remote_name: str, remote_branch_name: str) -> None:
    """
    Fetches a branch from the remote onto the local repository. Creates the branch if it doesn't exist.

    Args:
        repo: PyGitlet repository.
        remote_name: Name of the remote on the local repo.
        remote_branch_name: Name of the branch on the remote repo.
    """
    remote_folder = repo.branches / remote_name
    remote_gitlet = (remote_folder / ".gitlet").resolve()
    if not remote_gitlet.exists():
        raise PyGitletException("Remote directory not found.")
    repo_remote = Repository(remote_gitlet)

    if not (repo_remote.branches / remote_branch_name).exists():
        raise PyGitletException("That remote does not have that branch.")
    remote_branch: Branch = read_object(repo_remote.branches / remote_branch_name)

    remote_branch_history = commit_history(remote_branch.commit)
    for commit in remote_branch_history:
        write_object(repo.commits / commit.hash, commit)
        for blob_hash, blob in commit.file_blob_map.items():
            if not (repo.blobs / blob_hash).exists():
                write_object(repo.blobs / blob_hash, blob)
    remote_branch_on_local = Branch(
        remote_branch_name, remote_branch_history[0], False, remote=Path(remote_name)
    )
    with (repo.branches / remote_branch_on_local.name).open(mode="wb") as f:
        pickle.dump(remote_branch_on_local, f)


def pull(repo: Repository, remote_name: str, remote_branch_name: str) -> None:
    """
    Pulls a repository from remote onto the local repository, merging onto the current branch.

    Args:
        repo: PyGitlet repository.
        remote_name: Name of the remote on the local repo.
        remote_branch_name: Name of the branch on the remote repo.
    """
    fetch(repo, remote_name, remote_branch_name)
    merge(repo, f"{remote_name}/{remote_branch_name}")

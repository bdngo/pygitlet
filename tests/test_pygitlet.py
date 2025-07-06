import pickle
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest

from pygitlet import commands, errors


@pytest.fixture
def repo(tmp_path: Path) -> commands.Repository:
    return commands.Repository(gitlet=tmp_path / ".gitlet")


@pytest.fixture
def temp_file1(tmp_path: Path) -> Path:
    (tmp_path / "a.in").write_text("a")
    return Path("a.in")


@pytest.fixture
def temp_file2(tmp_path: Path) -> Path:
    (tmp_path / "b.in").write_text("b")
    return Path("b.in")


@pytest.fixture
def repo_committed(repo: commands.Repository, temp_file1: Path) -> commands.Repository:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")
    return repo


@pytest.fixture
def log_pattern() -> re.Pattern:
    return re.compile(r"(===\ncommit [0-9a-f]+\nDate: .+\n.+)+")


@pytest.fixture
def merge_log_pattern() -> re.Pattern:
    return re.compile(
        r"===\ncommit [0-9a-f]+\nMerge: [0-9a-f]{7} [0-9a-f{7}\nDate: .+\n.+"
    )


def test_successful_init(repo: commands.Repository) -> None:
    commands.init(repo)
    assert repo.gitlet.exists()
    assert repo.commits.exists()
    assert repo.blobs.exists()
    assert repo.stage.exists()
    assert repo.branches.exists()
    assert repo.current_branch.exists()


def test_unsuccessful_init(repo: commands.Repository) -> None:
    repo.gitlet.mkdir()
    with pytest.raises(
        errors.PyGitletException,
        match=r"A Gitlet version-control system already exists in the current directory\.",
    ):
        commands.init(repo)


def test_add(repo: commands.Repository, tmp_path: Path, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)

    assert (repo.stage / temp_file1.name).exists()
    with (repo.stage / temp_file1.name).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    contents = (tmp_path / temp_file1).read_text()

    assert blob.name == temp_file1
    assert blob.contents == contents
    assert blob.diff == commands.Diff.ADDED


def test_add_unchanged_file(
    repo_committed: commands.Repository, temp_file1: Path
) -> None:
    commands.add(repo_committed, temp_file1)
    assert len(list(repo_committed.stage.iterdir())) == 0


def test_add_missing_file(repo: commands.Repository, tmp_path: Path) -> None:
    commands.init(repo)

    with pytest.raises(errors.PyGitletException, match=r"File does not exist\."):
        commands.add(repo, tmp_path / "b.in")


def test_add_duplicate_file(
    repo_committed: commands.Repository, temp_file1: Path
) -> None:
    commands.add(repo_committed, temp_file1)
    assert len(list(repo_committed.stage.iterdir())) == 0


def test_add_removed_file(
    repo_committed: commands.Repository, temp_file1: Path
) -> None:
    commands.remove(repo_committed, temp_file1)
    commands.add(repo_committed, temp_file1)
    assert len(list(repo_committed.stage.iterdir())) == 1

    with (repo_committed.stage / temp_file1).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    assert blob.diff == commands.Diff.ADDED


def test_commit(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    assert len(list(repo.commits.iterdir())) == 1
    assert len(list(repo.blobs.iterdir())) == 0

    commands.add(repo, temp_file1)
    assert len(list(repo.stage.iterdir())) == 1

    message = "commit a.in"
    commands.commit(repo, message)
    assert len(list(repo.commits.iterdir())) == 2
    assert len(list(repo.blobs.iterdir())) == 1
    assert len(list(repo.stage.iterdir())) == 0

    current_branch = commands.get_current_branch(repo)
    assert current_branch.commit.message == message
    assert current_branch.commit.parent == commands.Commit(
        datetime.fromtimestamp(0, tz=timezone.utc).astimezone(), "initial commit", None
    )


def test_commit_changed_file(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed a.in")

    assert len(list(repo_committed.commits.iterdir())) == 3
    assert len(list(repo_committed.blobs.iterdir())) == 2

    current_commit = commands.get_current_branch(repo_committed).commit
    assert current_commit.message == "changed a.in"
    assert current_commit.parent.message == "commit a.in"

    with (repo_committed.blobs / current_commit.file_blob_map[temp_file1].hash).open(
        mode="rb"
    ) as f:
        changed_blob: commands.Blob = pickle.load(f)
    assert changed_blob.diff == commands.Diff.MODIFIED


def test_commit_removed_file(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    current_commit = commands.get_current_branch(repo_committed).commit
    with (repo_committed.blobs / current_commit.file_blob_map[temp_file1].hash).open(
        mode="rb"
    ) as f:
        tracked_blob: commands.Blob = pickle.load(f)
    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.remove(repo_committed, temp_file1)
    assert len(list(repo_committed.stage.iterdir())) == 1

    with (repo_committed.stage / temp_file1).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    assert blob.name == tracked_blob.name
    assert blob.contents == "b"
    assert blob.diff == commands.Diff.DELETED


def test_commit_multiple_files(
    repo: commands.Repository, temp_file1: Path, temp_file2: Path
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.add(repo, temp_file2)
    commands.commit(repo, "commit a.in and b.in")

    assert len(list(repo.commits.iterdir())) == 2
    assert len(list(repo.blobs.iterdir())) == 2


def test_commit_empty_stage(repo: commands.Repository) -> None:
    commands.init(repo)
    with pytest.raises(
        errors.PyGitletException, match=r"No changes added to the commit\."
    ):
        commands.commit(repo, "empty stage")


def test_commit_empty_message(repo: commands.Repository, temp_file1: None) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    with pytest.raises(
        errors.PyGitletException, match=r"Please enter a commit message\."
    ):
        commands.commit(repo, "")


def test_remove(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.remove(repo_committed, temp_file1)

    assert not (tmp_path / temp_file1).exists()
    assert len(list(repo_committed.stage.iterdir())) == 1

    with (repo_committed.stage / temp_file1.name).open(mode="rb") as f:
        removed_blob: commands.Blob = pickle.load(f)
    assert removed_blob.name == temp_file1
    assert removed_blob.diff == commands.Diff.DELETED


def test_remove_missing_file(repo: commands.Repository) -> None:
    commands.init(repo)

    with pytest.raises(
        errors.PyGitletException, match=r"No reason to remove the file\."
    ):
        commands.remove(repo, Path("b.in"))


def test_remove_untracked_file(
    repo_committed: commands.Repository, temp_file1: Path, temp_file2: Path
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"No reason to remove the file\."
    ):
        commands.remove(repo_committed, temp_file2)


def test_log_empty_repo(repo: commands.Repository, log_pattern: re.Pattern) -> None:
    commands.init(repo)
    log = commands.log(repo)
    assert len(list(re.finditer(log_pattern, log))) == 1


def test_log_with_commit(
    repo_committed: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 2


def test_log_only_current_head(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
    log_pattern: re.Pattern,
) -> None:
    commands.branch(repo_committed, "new")
    commands.checkout_branch(repo_committed, "new")

    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "commit on new branch")

    (tmp_path / temp_file1).write_text("c")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "commit on new branch again")

    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 4

    commands.checkout_branch(repo_committed, "main")
    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 2


@pytest.mark.skip(reason="resetting not implemented")
def test_log_with_reset(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
    log_pattern: re.Pattern,
) -> None:
    (tmp_path / temp_file1).write_text("b")
    commit_hash = commands.get_current_branch(repo_committed).commit.hash
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed a.in")
    commands.reset(repo_committed, commit_hash)

    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 2


@pytest.mark.skip(reason="merging not implemented")
def test_log_merge_commit(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
    log_pattern: re.Pattern,
    merge_log_pattern: re.Pattern,
) -> None:
    commands.branch("new")
    commands.checkout("new")
    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "commit on new branch")
    commands.checkout("main")
    commands.merge("new")
    log = commands.log(repo_committed)
    assert len(list(re.finditer(merge_log_pattern, log))) == 1
    assert len(list(re.finditer(log_pattern, log))) == 2


def test_global_log_single_branch(
    repo: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    commands.init(repo)
    log = commands.log(repo)
    global_log = commands.global_log(repo)
    assert log == global_log

    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")
    log = commands.log(repo)
    global_log = commands.global_log(repo)
    assert len(list(re.finditer(log_pattern, log))) == len(
        list(re.finditer(log_pattern, global_log))
    )


@pytest.mark.skip(reason="resetting not implemented")
def test_global_log_with_reset(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
    log_pattern: re.Pattern,
) -> None:
    (tmp_path / temp_file1).write_text("b")
    commit_hash = commands.get_current_branch(repo_committed).commit.hash
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed a.in")
    commands.reset(repo_committed, commit_hash)

    log = commands.log(repo_committed)
    global_log = commands.global_log(repo_committed)
    assert (
        len(list(re.finditer(log_pattern, log)))
        == len(list(re.finditer(log_pattern, global_log))) - 1
    )


def test_find(repo_committed: commands.Repository) -> None:
    current_commit = commands.get_current_branch(repo_committed).commit
    assert current_commit.hash == commands.find(repo_committed, current_commit.message)


def test_find_no_match(repo_committed: commands.Repository) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"Found no commit with that message\."
    ):
        commands.find(repo_committed, "blah")


def test_status_empty_repo(repo: commands.Repository) -> None:
    commands.init(repo)
    status = commands.status(repo)
    expected = dedent(
        """
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_multiple_branches(repo: commands.Repository) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    status = commands.status(repo)
    expected = dedent(
        """
    === Branches ===
    *main
    new

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected

    commands.checkout_branch(repo, "new")
    status = commands.status(repo)
    expected = dedent(
        """
    === Branches ===
    main
    *new

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_staged_for_addition(
    repo: commands.Repository, temp_file1: Path
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===
    {temp_file1.name}

    === Removed Files ===

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_staged_for_removal(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.remove(repo_committed, temp_file1)
    status = commands.status(repo_committed)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===
    {temp_file1.name}

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_modified_unstaged(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    (tmp_path / temp_file1).write_text("b")
    status = commands.status(repo_committed)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {temp_file1.name} (modified)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_deleted_unstaged(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    (tmp_path / temp_file1).unlink()
    status = commands.status(repo_committed)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {temp_file1.name} (deleted)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_modified_staged(
    repo: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    (tmp_path / temp_file1).write_text("b")
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===
    {temp_file1.name}

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {temp_file1.name} (modified)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_deleted_staged(
    repo: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    (tmp_path / temp_file1).unlink()
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===
    {temp_file1.name}

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {temp_file1.name} (deleted)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_untracked(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===

    === Untracked Files ===
    {temp_file1.name}"""
    ).strip()
    assert status == expected


def test_checkout_file(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    tracked_contents = (tmp_path / temp_file1).read_text()
    (tmp_path / temp_file1).write_text("b")
    commands.checkout_file(repo_committed, temp_file1)
    contents = (tmp_path / temp_file1).read_text()
    assert contents == tracked_contents


def test_checkout_file_untracked(
    repo_committed: commands.Repository,
    temp_file2: Path,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"File does not exist in that commit\."
    ):
        commands.checkout_file(repo_committed, temp_file2)


def test_checkout_commit_one_commit(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
) -> None:
    tracked_contents = (tmp_path / temp_file1).read_text()
    (tmp_path / temp_file1).write_text("b")
    current_commit = commands.get_current_branch(repo_committed).commit
    commands.checkout_commit(repo_committed, current_commit.hash, temp_file1)
    contents = (tmp_path / temp_file1).read_text()
    assert contents == tracked_contents


def test_checkout_commit_substring_hash(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
) -> None:
    tracked_contents = (tmp_path / temp_file1).read_text()
    (tmp_path / temp_file1).write_text("b")
    current_commit = commands.get_current_branch(repo_committed).commit
    commands.checkout_commit(repo_committed, current_commit.hash[:7], temp_file1)
    contents = (tmp_path / temp_file1).read_text()
    assert contents == tracked_contents


def test_checkout_commit_multiple_commits(
    repo_committed: commands.Repository,
    tmp_path: Path,
    temp_file1: Path,
) -> None:
    tracked_contents = (tmp_path / temp_file1).read_text()
    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed a.in")

    current_commit = commands.get_current_branch(repo_committed).commit
    parent_commit = current_commit.parent
    commands.checkout_commit(repo_committed, parent_commit.hash, temp_file1)
    contents = (tmp_path / temp_file1).read_text()
    assert contents == tracked_contents

    commands.checkout_commit(repo_committed, current_commit.hash, temp_file1)
    contents = (tmp_path / temp_file1).read_text()
    assert contents == "b"


def test_checkout_commit_untracked(
    repo_committed: commands.Repository,
    temp_file2: Path,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"File does not exist in that commit\."
    ):
        current_commit = commands.get_current_branch(repo_committed).commit
        commands.checkout_commit(repo_committed, current_commit.hash, temp_file2)


def test_checkout_commit_bad_id(
    repo_committed: commands.Repository,
    temp_file1: Path,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"No commit with that id exists\."
    ):
        commands.checkout_commit(repo_committed, "foobar", temp_file1)


def test_checkout_branch(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    old_contents = (tmp_path / temp_file1).read_text()
    commands.branch(repo_committed, "new")
    commands.checkout_branch(repo_committed, "new")

    (tmp_path / temp_file1).write_text("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed on new branch")

    commands.checkout_branch(repo_committed, "main")
    assert (tmp_path / temp_file1).read_text() == old_contents
    assert commands.get_current_branch(repo_committed).name == "main"

    commands.checkout_branch(repo_committed, "new")
    assert (tmp_path / temp_file1).read_text() == "b"
    assert commands.get_current_branch(repo_committed).name == "new"


def test_checkout_branch_nonexistent(repo: commands.Repository) -> None:
    commands.init(repo)
    with pytest.raises(errors.PyGitletException, match=r"No such branch exists\."):
        commands.checkout_branch(repo, "foo")


def test_checkout_branch_is_current(repo: commands.Repository) -> None:
    commands.init(repo)
    with pytest.raises(
        errors.PyGitletException, match=r"No need to checkout the current branch\."
    ):
        commands.checkout_branch(repo, commands.get_current_branch(repo).name)


def test_checkout_overwrite_untracked_file(
    repo: Path, tmp_path: Path, temp_file1: Path, temp_file2: Path
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, temp_file1)
    commands.add(repo, temp_file2)
    commands.commit(repo, "commit two files")

    (tmp_path / temp_file1).write_text("b")
    with pytest.raises(
        errors.PyGitletException,
        match=r"There is an untracked file in the way; delete it, or add and commit it first\.",
    ):
        commands.checkout_branch(repo, "new")


def test_branch_create(repo: Path) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    assert len(list(repo.branches.iterdir())) == 3


def test_branch_existing(repo: Path) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    with pytest.raises(
        errors.PyGitletException, match=r"A branch with that name already exists\."
    ):
        commands.branch(repo, "new")
    assert len(list(repo.branches.iterdir())) == 3


def test_remove_branch(repo: Path) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.remove_branch(repo, "new")
    assert len(list(repo.branches.iterdir())) == 2


def test_remove_branch_current(repo: Path) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    with pytest.raises(
        errors.PyGitletException, match=r"Cannot remove the current branch\."
    ):
        commands.remove_branch(repo, "main")

    commands.checkout_branch(repo, "new")
    with pytest.raises(
        errors.PyGitletException, match=r"Cannot remove the current branch\."
    ):
        commands.remove_branch(repo, "new")


def test_remove_branch_nonexistent(repo: Path) -> None:
    commands.init(repo)
    with pytest.raises(
        errors.PyGitletException, match=r"A branch with that name does not exist\."
    ):
        commands.remove_branch(repo, "new")

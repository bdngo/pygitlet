import pickle
import re
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest

from pygitlet import commands, errors


def test_init_successful(repo: commands.Repository) -> None:
    commands.init(repo)
    assert repo.gitlet.exists()
    assert repo.commits.exists()
    assert repo.blobs.exists()
    assert repo.stage.exists()
    assert repo.branches.exists()
    assert repo.current_branch.exists()
    assert repo.remotes.exists()
    assert commands.get_current_branch(repo).name == "main"


def test_init_unsuccessful(repo: commands.Repository) -> None:
    repo.gitlet.mkdir()
    with pytest.raises(
        errors.PyGitletException,
        match=r"A Gitlet version-control system already exists in the current directory\.",
    ):
        commands.init(repo)


def test_add(repo: commands.Repository, tmp_path: Path, tmp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)

    assert (repo.stage / tmp_file1.name).exists()
    with (repo.stage / tmp_file1.name).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    contents = (tmp_path / tmp_file1).read_text()

    assert blob.name == tmp_file1
    assert blob.contents == contents
    assert blob.diff == commands.Diff.ADDED


def test_add_unchanged_file(
    repo_commit_tmp_file1: commands.Repository, tmp_file1: Path
) -> None:
    commands.add(repo_commit_tmp_file1, tmp_file1)
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 0


def test_add_missing_file(repo: commands.Repository, tmp_path: Path) -> None:
    commands.init(repo)

    with pytest.raises(errors.PyGitletException, match=r"File does not exist\."):
        commands.add(repo, tmp_path / "b.in")


def test_add_duplicate_file(
    repo_commit_tmp_file1: commands.Repository, tmp_file1: Path
) -> None:
    commands.add(repo_commit_tmp_file1, tmp_file1)
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 0


def test_add_removed_file(
    repo_commit_tmp_file1: commands.Repository, tmp_file1: Path
) -> None:
    commands.remove(repo_commit_tmp_file1, tmp_file1)
    commands.add(repo_commit_tmp_file1, tmp_file1)
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 1

    with (repo_commit_tmp_file1.stage / tmp_file1).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    assert blob.diff == commands.Diff.ADDED


def test_commit(repo: commands.Repository, tmp_file1: Path) -> None:
    commands.init(repo)
    assert len(list(repo.commits.iterdir())) == 1
    assert len(list(repo.blobs.iterdir())) == 0

    commands.add(repo, tmp_file1)
    assert len(list(repo.stage.iterdir())) == 1

    message = "commit a.in"
    commands.commit(repo, message)
    assert len(list(repo.commits.iterdir())) == 2
    assert len(list(repo.blobs.iterdir())) == 1
    assert len(list(repo.stage.iterdir())) == 0

    current_branch = commands.get_current_branch(repo)
    assert current_branch.commit.message == message
    assert current_branch.commit.parents[0] == commands.Commit(
        datetime.fromtimestamp(0, tz=timezone.utc).astimezone(),
        "initial commit",
    )


def test_commit_changed_file(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed a.in")

    assert len(list(repo_commit_tmp_file1.commits.iterdir())) == 3
    assert len(list(repo_commit_tmp_file1.blobs.iterdir())) == 2

    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    assert current_commit.message == "changed a.in"
    assert current_commit.parents[0].message == "commit a.in"

    with (
        repo_commit_tmp_file1.blobs / current_commit.file_blob_map[tmp_file1].hash
    ).open(mode="rb") as f:
        changed_blob: commands.Blob = pickle.load(f)
    assert changed_blob.diff == commands.Diff.MODIFIED


def test_commit_removed_file(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    with (
        repo_commit_tmp_file1.blobs / current_commit.file_blob_map[tmp_file1].hash
    ).open(mode="rb") as f:
        tracked_blob: commands.Blob = pickle.load(f)
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.remove(repo_commit_tmp_file1, tmp_file1)
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 1

    with (repo_commit_tmp_file1.stage / tmp_file1).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    assert blob.name == tracked_blob.name
    assert blob.contents == "b\n"
    assert blob.diff == commands.Diff.DELETED


def test_commit_multiple_files(
    repo: commands.Repository, tmp_file1: Path, tmp_file2: Path
) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit a.in and b.in")

    assert len(list(repo.commits.iterdir())) == 2
    assert len(list(repo.blobs.iterdir())) == 2


def test_commit_empty_stage(repo: commands.Repository) -> None:
    commands.init(repo)
    with pytest.raises(
        errors.PyGitletException, match=r"No changes added to the commit\."
    ):
        commands.commit(repo, "empty stage")


def test_commit_empty_message(repo: commands.Repository, tmp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    with pytest.raises(
        errors.PyGitletException, match=r"Please enter a commit message\."
    ):
        commands.commit(repo, "")


def test_remove(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.remove(repo_commit_tmp_file1, tmp_file1)

    assert not (tmp_path / tmp_file1).exists()
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 1

    with (repo_commit_tmp_file1.stage / tmp_file1.name).open(mode="rb") as f:
        removed_blob: commands.Blob = pickle.load(f)
    assert removed_blob.name == tmp_file1
    assert removed_blob.diff == commands.Diff.DELETED


def test_remove_missing_file(repo: commands.Repository) -> None:
    commands.init(repo)

    with pytest.raises(
        errors.PyGitletException, match=r"No reason to remove the file\."
    ):
        commands.remove(repo, Path("b.in"))


def test_remove_untracked_file(
    repo_commit_tmp_file1: commands.Repository, tmp_file1: Path, tmp_file2: Path
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"No reason to remove the file\."
    ):
        commands.remove(repo_commit_tmp_file1, tmp_file2)


def test_log_empty_repo(repo: commands.Repository, log_pattern: re.Pattern) -> None:
    commands.init(repo)
    log = commands.log(repo)
    assert len(list(re.finditer(log_pattern, log))) == 1


def test_log_with_commit(
    repo_commit_tmp_file1: commands.Repository, tmp_file1: Path, log_pattern: re.Pattern
) -> None:
    log = commands.log(repo_commit_tmp_file1)
    assert len(list(re.finditer(log_pattern, log))) == 2


def test_log_only_current_head(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    log_pattern: re.Pattern,
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")
    commands.checkout_branch(repo_commit_tmp_file1, "new")

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "commit on new branch")

    (tmp_path / tmp_file1).write_text("c\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "commit on new branch again")

    log = commands.log(repo_commit_tmp_file1)
    assert len(list(re.finditer(log_pattern, log))) == 4

    commands.checkout_branch(repo_commit_tmp_file1, "main")
    log = commands.log(repo_commit_tmp_file1)
    assert len(list(re.finditer(log_pattern, log))) == 2


def test_log_with_reset(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    log_pattern: re.Pattern,
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    commit_hash = commands.get_current_branch(repo_commit_tmp_file1).commit.hash
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed a.in")
    commands.reset(repo_commit_tmp_file1, commit_hash)

    log = commands.log(repo_commit_tmp_file1)
    assert len(list(re.finditer(log_pattern, log))) == 2


def test_log_merge_commit(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
    log_pattern: re.Pattern,
    merge_log_pattern: re.Pattern,
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")

    commands.checkout_branch(repo, "new")
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit b.in")

    commands.checkout_branch(repo, "main")
    commands.merge(repo, "new")
    log = commands.log(repo)
    print(log)
    assert len(list(re.finditer(merge_log_pattern, log))) == 1
    assert len(list(re.finditer(log_pattern, log))) == 2


def test_global_log_single_branch(
    repo: commands.Repository, tmp_file1: Path, log_pattern: re.Pattern
) -> None:
    commands.init(repo)
    log = commands.log(repo)
    global_log = commands.global_log(repo)
    assert log == global_log

    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")
    log = commands.log(repo)
    global_log = commands.global_log(repo)
    assert len(list(re.finditer(log_pattern, log))) == len(
        list(re.finditer(log_pattern, global_log))
    )


def test_global_log_with_reset(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    log_pattern: re.Pattern,
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    commit_hash = commands.get_current_branch(repo_commit_tmp_file1).commit.hash
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed a.in")
    commands.reset(repo_commit_tmp_file1, commit_hash)

    log = commands.log(repo_commit_tmp_file1)
    global_log = commands.global_log(repo_commit_tmp_file1)
    assert (
        len(list(re.finditer(log_pattern, log)))
        == len(list(re.finditer(log_pattern, global_log))) - 1
    )


def test_find(repo_commit_tmp_file1: commands.Repository) -> None:
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    assert current_commit.hash == commands.find(
        repo_commit_tmp_file1, current_commit.message
    )


def test_find_no_match(repo_commit_tmp_file1: commands.Repository) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"Found no commit with that message\."
    ):
        commands.find(repo_commit_tmp_file1, "blah")


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


def test_status_staged_for_addition(repo: commands.Repository, tmp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===
    {tmp_file1.name}

    === Removed Files ===

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_staged_for_removal(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.remove(repo_commit_tmp_file1, tmp_file1)
    status = commands.status(repo_commit_tmp_file1)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===
    {tmp_file1.name}

    === Modifications Not Staged For Commit ===

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_modified_unstaged(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    status = commands.status(repo_commit_tmp_file1)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {tmp_file1.name} (modified)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_deleted_unstaged(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    (tmp_path / tmp_file1).unlink()
    status = commands.status(repo_commit_tmp_file1)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {tmp_file1.name} (deleted)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_modified_staged(
    repo: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    (tmp_path / tmp_file1).write_text("b\n")
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===
    {tmp_file1.name}

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {tmp_file1.name} (modified)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_deleted_staged(
    repo: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    (tmp_path / tmp_file1).unlink()
    status = commands.status(repo)
    expected = dedent(
        f"""
    === Branches ===
    *main

    === Staged Files ===
    {tmp_file1.name}

    === Removed Files ===

    === Modifications Not Staged For Commit ===
    {tmp_file1.name} (deleted)

    === Untracked Files ==="""
    ).strip()
    assert status == expected


def test_status_untracked(repo: commands.Repository, tmp_file1: Path) -> None:
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
    {tmp_file1.name}"""
    ).strip()
    assert status == expected


def test_checkout_file(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    tracked_contents = (tmp_path / tmp_file1).read_text()
    (tmp_path / tmp_file1).write_text("b\n")
    commands.checkout_file(repo_commit_tmp_file1, tmp_file1)
    contents = (tmp_path / tmp_file1).read_text()
    assert contents == tracked_contents


def test_checkout_file_untracked(
    repo_commit_tmp_file1: commands.Repository,
    tmp_file2: Path,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"File does not exist in that commit\."
    ):
        commands.checkout_file(repo_commit_tmp_file1, tmp_file2)


def test_checkout_commit_one_commit(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
) -> None:
    tracked_contents = (tmp_path / tmp_file1).read_text()
    (tmp_path / tmp_file1).write_text("b\n")
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    commands.checkout_commit(repo_commit_tmp_file1, current_commit.hash, tmp_file1)
    contents = (tmp_path / tmp_file1).read_text()
    assert contents == tracked_contents


def test_checkout_commit_substring_hash(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
) -> None:
    tracked_contents = (tmp_path / tmp_file1).read_text()
    (tmp_path / tmp_file1).write_text("b\n")
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    commands.checkout_commit(repo_commit_tmp_file1, current_commit.hash[:7], tmp_file1)
    contents = (tmp_path / tmp_file1).read_text()
    assert contents == tracked_contents


def test_checkout_commit_multiple_commits(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
) -> None:
    tracked_contents = (tmp_path / tmp_file1).read_text()
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed a.in")

    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    parent_commit = current_commit.parents[0]
    commands.checkout_commit(repo_commit_tmp_file1, parent_commit.hash, tmp_file1)
    contents = (tmp_path / tmp_file1).read_text()
    assert contents == tracked_contents

    commands.checkout_commit(repo_commit_tmp_file1, current_commit.hash, tmp_file1)
    contents = (tmp_path / tmp_file1).read_text()
    assert contents == "b\n"


def test_checkout_commit_untracked(
    repo_commit_tmp_file1: commands.Repository,
    tmp_file2: Path,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"File does not exist in that commit\."
    ):
        current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
        commands.checkout_commit(repo_commit_tmp_file1, current_commit.hash, tmp_file2)


def test_checkout_commit_bad_id(
    repo_commit_tmp_file1: commands.Repository,
    tmp_file1: Path,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"No commit with that id exists\."
    ):
        commands.checkout_commit(repo_commit_tmp_file1, "foobar", tmp_file1)


def test_checkout_branch(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    old_contents = (tmp_path / tmp_file1).read_text()
    commands.branch(repo_commit_tmp_file1, "new")
    commands.checkout_branch(repo_commit_tmp_file1, "new")

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed on new branch")

    commands.checkout_branch(repo_commit_tmp_file1, "main")
    assert (tmp_path / tmp_file1).read_text() == old_contents
    assert commands.get_current_branch(repo_commit_tmp_file1).name == "main"

    commands.checkout_branch(repo_commit_tmp_file1, "new")
    assert (tmp_path / tmp_file1).read_text() == "b\n"
    assert commands.get_current_branch(repo_commit_tmp_file1).name == "new"


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
    repo: commands.Repository, tmp_path: Path, tmp_file1: Path, tmp_file2: Path
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit two files")

    commands.checkout_branch(repo, "new")
    (tmp_path / tmp_file1).write_text("b\n")
    with pytest.raises(
        errors.PyGitletException,
        match=r"There is an untracked file in the way; delete it, or add and commit it first\.",
    ):
        commands.checkout_branch(repo, "main")


def test_checkout_branch_empty_stage(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")
    commands.checkout_branch(repo_commit_tmp_file1, "new")

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed on new branch")

    commands.add(repo_commit_tmp_file1, tmp_file2)
    commands.checkout_branch(repo_commit_tmp_file1, "main")
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 0


def test_branch_create(repo: commands.Repository) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    assert len(list(repo.branches.iterdir())) == 3


def test_branch_existing(repo: commands.Repository) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    with pytest.raises(
        errors.PyGitletException, match=r"A branch with that name already exists\."
    ):
        commands.branch(repo, "new")
    assert len(list(repo.branches.iterdir())) == 3


def test_remove_branch(repo: commands.Repository) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.remove_branch(repo, "new")
    assert len(list(repo.branches.iterdir())) == 2


def test_remove_branch_current(repo: commands.Repository) -> None:
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


def test_remove_branch_nonexistent(repo: commands.Repository) -> None:
    commands.init(repo)
    with pytest.raises(
        errors.PyGitletException, match=r"A branch with that name does not exist\."
    ):
        commands.remove_branch(repo, "new")


def test_reset(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    old_contents = (tmp_path / tmp_file1).read_text()
    (tmp_path / tmp_file1).write_text("b\n")
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed a.in")
    commands.reset(repo_commit_tmp_file1, current_commit.hash)

    assert (tmp_path / tmp_file1).read_text() == old_contents


def test_reset_nonexistent(
    repo_commit_tmp_file1: commands.Repository, tmp_path: Path, tmp_file1: Path
) -> None:
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed a.in")

    with pytest.raises(
        errors.PyGitletException, match=r"No commit with that id exists\."
    ):
        commands.reset(repo_commit_tmp_file1, "foobar")


def test_reset_overwrite_untracked_file(
    repo: commands.Repository, tmp_path: Path, tmp_file1: Path, tmp_file2: Path
) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit two files")

    commands.branch(repo, "new")
    (tmp_path / "c.in").write_text("c\n")
    commands.add(repo, Path("c.in"))
    commands.remove(repo, tmp_file2)
    commands.commit(repo, "add c.in, remove b.in")
    current_commit = commands.get_current_branch(repo).commit

    commands.checkout_branch(repo, "new")
    (tmp_path / "c.in").write_text("d\n")
    with pytest.raises(
        errors.PyGitletException,
        match=r"There is an untracked file in the way; delete it, or add and commit it first\.",
    ):
        commands.reset(repo, current_commit.hash)


def test_reset_empty_stage(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "changed on new branch")

    commands.add(repo_commit_tmp_file1, tmp_file2)
    commands.reset(repo_commit_tmp_file1, current_commit.hash)
    assert len(list(repo_commit_tmp_file1.stage.iterdir())) == 0


def test_reset_removed_file(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    current_commit = commands.get_current_branch(repo_commit_tmp_file1).commit
    commands.add(repo_commit_tmp_file1, tmp_file2)
    commands.commit(repo_commit_tmp_file1, "add b.in")
    commands.reset(repo_commit_tmp_file1, current_commit.hash)
    assert not (tmp_path / tmp_file2).exists()


def test_merge(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")
    main_commit = commands.get_current_branch(repo).commit

    commands.checkout_branch(repo, "new")
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit b.in on new branch")
    new_commit = commands.get_current_branch(repo).commit

    commands.checkout_branch(repo, "main")
    commands.merge(repo, "new")

    merge_commit = commands.get_current_branch(repo).commit
    assert merge_commit.parents == [main_commit, new_commit]
    assert (
        tmp_file1 in merge_commit.file_blob_map
        and tmp_file2 in merge_commit.file_blob_map
    )


def test_merge_nonexistent(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")

    commands.checkout_branch(repo, "new")
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit b.in on new branch")

    commands.checkout_branch(repo, "main")
    with pytest.raises(
        errors.PyGitletException,
        match=r"A branch with that name does not exist\.",
    ):
        commands.merge(repo, "foo")


def test_merge_nonempty_stage(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")

    commands.checkout_branch(repo, "new")
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit b.in on new branch")

    commands.checkout_branch(repo, "main")
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo, tmp_file1)

    with pytest.raises(
        errors.PyGitletException, match=r"You have uncommitted changes\."
    ):
        commands.merge(repo, "new")


def test_merge_self(
    repo_commit_tmp_file1: commands.Repository,
) -> None:
    with pytest.raises(
        errors.PyGitletException, match=r"Cannot merge a branch with itself\."
    ):
        commands.merge(repo_commit_tmp_file1, "main")


def test_merge_untracked_file(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    commands.add(repo, tmp_file2)
    commands.commit(repo, "commit two files")
    commands.branch(repo, "new")

    (tmp_path / "c.in").write_text("c\n")
    commands.add(repo, Path("c.in"))
    commands.remove(repo, tmp_file2)
    commands.commit(repo, "add c.in, remove b.in on main")

    commands.checkout_branch(repo, "new")
    commands.remove(repo, tmp_file1)
    (tmp_path / "d.in").write_text("d\n")
    commands.add(repo, Path("d.in"))
    commands.commit(repo, "add d.in, remove a.in on new")

    commands.checkout_branch(repo, "main")
    (tmp_path / "d.in").write_text("a\n")

    with pytest.raises(
        errors.PyGitletException,
        match=r"There is an untracked file in the way; delete it, or add and commit it first\.",
    ):
        commands.merge(repo, "new")


def test_merge_target_is_ancestor(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")
    commands.merge(repo, "new")
    captured = capsys.readouterr()
    assert captured.out.strip() == "Given branch is an ancestor of the current branch."


def test_merge_fast_forward(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")
    commands.checkout_branch(repo, "new")
    commands.merge(repo, "main")
    captured = capsys.readouterr()
    assert captured.out.strip() == "Current branch fast-forwarded."


def test_merge_criss_cross(
    repo: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands.init(repo)
    commands.branch(repo, "new")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in on main")

    commands.checkout_branch(repo, "new")
    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in on new")

    commands.branch(repo, "temp")
    commands.merge(repo, "main")
    captured = capsys.readouterr()
    assert captured.out.strip() == "Encountered a merge conflict."
    conflicted_tmp_file1 = "<<<<<<< HEAD\nb\n=======\na\n>>>>>>>\n"
    assert (tmp_path / tmp_file1).read_text() == conflicted_tmp_file1

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "reset conflicted a.in on new")
    commands.remove(repo, tmp_file1)
    commands.commit(repo, "remove a.in on new")

    commands.checkout_branch(repo, "main")
    commands.add(repo, tmp_file2)
    commands.commit(repo, "add b.in on main")
    commands.merge(repo, "temp")

    (tmp_path / tmp_file2).write_text("c\n")
    commands.add(repo, tmp_file2)
    commands.commit(repo, "changed b.in on main")
    assert captured.out.strip() == "Encountered a merge conflict."
    conflicted_tmp_file1 = "<<<<<<< HEAD\na\n=======\nb\n>>>>>>>\n"
    assert (tmp_path / tmp_file1).read_text() == conflicted_tmp_file1

    (tmp_path / tmp_file1).write_text("a\n")
    commands.add(repo, tmp_file1)
    commands.commit(repo, "reset conflicted a.in on main")
    commands.merge(repo, "new")
    assert captured.out.strip() == "Encountered a merge conflict."
    conflicted_tmp_file1 = "<<<<<<< HEAD\na\n=======\n\n>>>>>>>\n"
    assert (tmp_path / tmp_file1).read_text() == conflicted_tmp_file1
    assert (tmp_path / tmp_file2).read_text() == "c\n"


def test_merge_conflict_deleted_modified(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "change a.in on main")

    commands.checkout_branch(repo_commit_tmp_file1, "new")
    commands.remove(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "remove a.in on new")

    commands.merge(repo_commit_tmp_file1, "main")
    captured = capsys.readouterr()
    assert captured.out.strip() == "Encountered a merge conflict."
    conflicted_tmp_file1 = "<<<<<<< HEAD\n\n=======\nb\n>>>>>>>\n"
    assert (tmp_path / tmp_file1).read_text() == conflicted_tmp_file1


def test_merge_conflict_deleted_modified_2(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "change a.in on main")

    commands.checkout_branch(repo_commit_tmp_file1, "new")
    commands.remove(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "remove a.in on new")

    commands.checkout_branch(repo_commit_tmp_file1, "main")
    commands.merge(repo_commit_tmp_file1, "new")
    captured = capsys.readouterr()
    assert captured.out.strip() == "Encountered a merge conflict."
    conflicted_tmp_file1 = "<<<<<<< HEAD\nb\n=======\n\n>>>>>>>\n"
    assert (tmp_path / tmp_file1).read_text() == conflicted_tmp_file1


def test_merge_conflict_both_modified(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")

    (tmp_path / tmp_file1).write_text("b\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "change a.in on main")

    commands.checkout_branch(repo_commit_tmp_file1, "new")
    (tmp_path / tmp_file1).write_text("c\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "change a.in on new")

    commands.merge(repo_commit_tmp_file1, "main")
    captured = capsys.readouterr()
    assert captured.out.strip() == "Encountered a merge conflict."
    conflicted_tmp_file1 = "<<<<<<< HEAD\nc\n=======\nb\n>>>>>>>\n"
    assert (tmp_path / tmp_file1).read_text() == conflicted_tmp_file1


def test_merge_conflict_unchanged_modified(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")
    commands.add(repo_commit_tmp_file1, tmp_file2)
    commands.commit(repo_commit_tmp_file1, "add b.in on main")

    commands.checkout_branch(repo_commit_tmp_file1, "new")
    (tmp_path / tmp_file1).write_text("c\n")
    commands.add(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "change a.in on new")

    commands.checkout_branch(repo_commit_tmp_file1, "main")
    commands.merge(repo_commit_tmp_file1, "new")

    assert (tmp_path / tmp_file1).read_text() == "c\n"
    assert (tmp_path / tmp_file2).read_text() == "b\n"


def test_merge_conflict_unchanged_deleted(
    repo_commit_tmp_file1: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.branch(repo_commit_tmp_file1, "new")
    commands.add(repo_commit_tmp_file1, tmp_file2)
    commands.commit(repo_commit_tmp_file1, "add b.in on main")

    commands.checkout_branch(repo_commit_tmp_file1, "new")
    commands.remove(repo_commit_tmp_file1, tmp_file1)
    commands.commit(repo_commit_tmp_file1, "remove a.in on new")

    commands.checkout_branch(repo_commit_tmp_file1, "main")
    commands.merge(repo_commit_tmp_file1, "new")

    assert not (tmp_path / tmp_file1).exists()
    assert (tmp_path / tmp_file2).read_text() == "b\n"


def test_add_remote(
    repo: commands.Repository, repo_remote: commands.Repository
) -> None:
    commands.init(repo)
    commands.add_remote(repo, "remote", repo_remote)
    assert (repo.remotes / "remote").exists()


def test_add_remote_existing(
    repo: commands.Repository, repo_remote: commands.Repository
) -> None:
    commands.init(repo)
    commands.add_remote(repo, "remote", repo_remote)
    with pytest.raises(
        errors.PyGitletException, match=r"A remote with that name already exists\."
    ):
        commands.add_remote(repo, "remote", repo_remote)


def test_remove_remote(
    repo: commands.Repository, repo_remote: commands.Repository
) -> None:
    commands.init(repo)
    commands.add_remote(repo, "remote", repo_remote)
    assert (repo.remotes / "remote").exists()
    commands.remove_remote(repo, "remote")
    assert not (repo.remotes / "remote").exists()


def test_remove_remote_nonexistent(repo: commands.Repository) -> None:
    commands.init(repo)
    with pytest.raises(
        errors.PyGitletException, match=r"A remote with that name does not exist\."
    ):
        commands.remove_remote(repo, "remote")


def test_push(
    repo: commands.Repository,
    repo_remote: commands.Repository,
    tmp_path: Path,
    tmp_file1: Path,
    tmp_file2: Path,
) -> None:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    commands.add(repo, tmp_file2)
    commands.commit(repo, "add a.in and b.in")

    commands.init(repo_remote)
    remote_file = repo_remote.gitlet.parent / "c.in"
    remote_file.write_text("c\n")
    commands.add(repo_remote, Path("c.in"))
    commands.commit(repo_remote, "add c.in on remote")

    commands.add_remote(repo_remote, "remote", repo.gitlet)
    commands.fetch(repo_remote, "remote", "main")
    commands.checkout_branch(repo_remote, "remote/main")
    commit_hash = commands.get_current_branch(repo_remote).commit.hash
    commands.checkout_branch(repo_remote, "main")
    commands.reset(repo_remote, commit_hash)

    (repo_remote.gitlet.parent / "d.in").write_text("d\n")
    commands.add(repo_remote, "d.in")
    commands.commit(repo_remote, "commit d.in on remote")
    commands.push(repo_remote, "remote", "main")

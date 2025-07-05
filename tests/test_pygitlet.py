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
    with (tmp_path / "a.in").open(mode="w") as f:
        f.write("a")
    return tmp_path / "a.in"


@pytest.fixture
def temp_file2(tmp_path: Path) -> Path:
    with (tmp_path / "b.in").open(mode="w") as f:
        f.write("b")
    return tmp_path / "b.in"


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


def test_add(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)

    assert (repo.stage / temp_file1.name).exists()
    with (repo.stage / temp_file1.name).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    with temp_file1.open() as f:
        contents = f.read()

    assert blob.name == temp_file1
    assert blob.contents == contents
    assert blob.diff == commands.Diff.ADDED


def test_add_unchanged_file(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.add(repo, temp_file1)
    assert len(list(repo.stage.iterdir())) == 0


def test_add_missing_file(repo: commands.Repository, tmp_path: Path) -> None:
    commands.init(repo)

    with pytest.raises(errors.PyGitletException, match=r"File does not exist\."):
        commands.add(repo, tmp_path / "b.in")


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


def test_commit_no_duplicate_blob(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in again")
    assert len(list(repo.commits.iterdir())) == 3
    assert len(list(repo.blobs.iterdir())) == 1


def test_commit_changed_file(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")
    with temp_file1.open(mode="w") as f:
        f.write("b")
    commands.add(repo, temp_file1)
    commands.commit(repo, "changed a.in")

    assert len(list(repo.commits.iterdir())) == 3
    assert len(list(repo.blobs.iterdir())) == 2

    current_branch = commands.get_current_branch(repo)
    assert current_branch.commit.message == "changed a.in"
    assert current_branch.commit.parent.message == "commit a.in"

    with (repo.blobs / current_branch.commit.file_blob_map[temp_file1]).open(
        mode="rb"
    ) as f:
        changed_blob: commands.Blob = pickle.load(f)
    assert changed_blob.diff == commands.Diff.CHANGED


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


def test_remove(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")
    commands.remove(repo, temp_file1)

    assert not temp_file1.exists()
    assert len(list(repo.stage.iterdir())) == 1

    with (repo.stage / temp_file1.name).open(mode="rb") as f:
        removed_blob: commands.Blob = pickle.load(f)
    assert removed_blob.name == temp_file1
    assert removed_blob.diff == commands.Diff.REMOVED


def test_remove_missing_file(repo: commands.Repository, tmp_path: Path) -> None:
    commands.init(repo)

    with pytest.raises(
        errors.PyGitletException, match=r"No reason to remove the file\."
    ):
        commands.remove(repo, tmp_path / "b.in")


def test_remove_untracked_file(
    repo: commands.Repository, temp_file1: Path, temp_file2: Path
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")

    with pytest.raises(
        errors.PyGitletException, match=r"No reason to remove the file\."
    ):
        commands.remove(repo, temp_file2)


def test_log_empty_repo(repo: commands.Repository, log_pattern: re.Pattern) -> None:
    commands.init(repo)
    log = commands.log(repo)
    assert len(list(re.finditer(log_pattern, log))) == 1


def test_log_with_commit(
    repo: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")
    log = commands.log(repo)
    assert len(list(re.finditer(log_pattern, log))) == 2


@pytest.mark.skip(reason="branching not implmented")
def test_log_only_current_head(
    repo: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")

    commands.branch("new")
    commands.checkout("new")
    with temp_file1.open(mode="w") as f:
        f.write("b")
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit on new branch")
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit on new branch again")
    log = commands.log(repo)
    assert len(list(re.finditer(log_pattern, log))) == 3

    commands.checkout("main")
    log = commands.log(repo)
    assert len(list(re.finditer(log_pattern, log))) == 2


@pytest.mark.skip(reason="merge not implemented")
def test_log_merge_commit(
    repo: commands.Repository,
    temp_file1: Path,
    log_pattern: re.Pattern,
    merge_log_pattern: re.Pattern,
) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit a.in")

    commands.branch("new")
    commands.checkout("new")
    with temp_file1.open(mode="w") as f:
        f.write("b")
    commands.add(repo, temp_file1)
    commands.commit(repo, "commit on new branch")
    commands.checkout("main")
    commands.merge("new")
    log = commands.log(repo)
    assert len(list(re.finditer(merge_log_pattern, log))) == 1
    assert len(list(re.finditer(log_pattern, log))) == 2

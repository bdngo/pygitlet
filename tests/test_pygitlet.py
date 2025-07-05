import pickle
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pygitlet import commands, errors


@pytest.fixture
def repo(tmp_path: Path) -> commands.Repository:
    return commands.Repository(gitlet=tmp_path / ".gitlet")


@pytest.fixture
def temp_file1(tmp_path: Path) -> Path:
    with (tmp_path / "a.in").open(mode="w") as f:
        f.write("a")
    return Path("a.in")


@pytest.fixture
def temp_file2(tmp_path: Path) -> Path:
    with (tmp_path / "b.in").open(mode="w") as f:
        f.write("b")
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
    with (tmp_path / temp_file1).open() as f:
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


def test_commit_no_duplicate_blob(
    repo_committed: commands.Repository, temp_file1: Path
) -> None:
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "commit a.in again")
    assert len(list(repo_committed.commits.iterdir())) == 3
    assert len(list(repo_committed.blobs.iterdir())) == 1


def test_commit_changed_file(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    with (tmp_path / temp_file1).open(mode="w") as f:
        f.write("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed a.in")

    assert len(list(repo_committed.commits.iterdir())) == 3
    assert len(list(repo_committed.blobs.iterdir())) == 2

    current_branch = commands.get_current_branch(repo_committed)
    assert current_branch.commit.message == "changed a.in"
    assert current_branch.commit.parent.message == "commit a.in"

    with (repo_committed.blobs / current_branch.commit.file_blob_map[temp_file1]).open(
        mode="rb"
    ) as f:
        changed_blob: commands.Blob = pickle.load(f)
    assert changed_blob.diff == commands.Diff.CHANGED


def test_commit_removed_file(
    repo_committed: commands.Repository, tmp_path: Path, temp_file1: Path
) -> None:
    current_commit = commands.get_current_branch(repo_committed).commit
    with (repo_committed.blobs / current_commit.file_blob_map[temp_file1]).open(
        mode="rb"
    ) as f:
        tracked_blob: commands.Blob = pickle.load(f)
    commands.add(repo_committed, temp_file1)
    commands.remove(repo_committed, temp_file1)
    assert len(list(repo_committed.stage.iterdir())) == 1

    with (repo_committed.stage / temp_file1).open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    assert blob.name == tracked_blob.name
    assert blob.contents == tracked_blob.contents
    assert blob.hash == tracked_blob.hash
    assert blob.diff == commands.Diff.REMOVED


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
    commands.add(repo_committed, temp_file1)
    commands.remove(repo_committed, temp_file1)

    assert not (tmp_path / temp_file1).exists()
    assert len(list(repo_committed.stage.iterdir())) == 1

    with (repo_committed.stage / temp_file1.name).open(mode="rb") as f:
        removed_blob: commands.Blob = pickle.load(f)
    assert removed_blob.name == temp_file1
    assert removed_blob.diff == commands.Diff.REMOVED


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


@pytest.mark.skip(reason="branching not implmented")
def test_log_only_current_head(
    repo_committed: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    commands.branch("new")
    commands.checkout("new")
    with temp_file1.open(mode="w") as f:
        f.write("b")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "commit on new branch")
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "commit on new branch again")
    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 3

    commands.checkout("main")
    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 2


@pytest.mark.skip(reason="resetting not implemented")
def test_log_with_reset(
    repo_committed: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    with temp_file1.open(mode="w") as f:
        f.write("b")
    commit_hash = commands.get_current_branch(repo_committed).commit.hash
    commands.add(repo_committed, temp_file1)
    commands.commit(repo_committed, "changed a.in")
    commands.reset(repo_committed, commit_hash)

    log = commands.log(repo_committed)
    assert len(list(re.finditer(log_pattern, log))) == 2


@pytest.mark.skip(reason="merge not implemented")
def test_log_merge_commit(
    repo_committed: commands.Repository,
    temp_file1: Path,
    log_pattern: re.Pattern,
    merge_log_pattern: re.Pattern,
) -> None:
    commands.branch("new")
    commands.checkout("new")
    with temp_file1.open(mode="w") as f:
        f.write("b")
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
    repo_committed: commands.Repository, temp_file1: Path, log_pattern: re.Pattern
) -> None:
    with temp_file1.open(mode="w") as f:
        f.write("b")
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

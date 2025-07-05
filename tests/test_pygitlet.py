import pickle
from datetime import datetime
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
    return tmp_path / "a.in"


@pytest.fixture
def temp_file2(tmp_path: Path) -> Path:
    with (tmp_path / "b.in").open(mode="w") as f:
        f.write("b")
    return tmp_path / "b.in"


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

    assert (repo.stage / "a.in").exists()
    with (repo.stage / "a.in").open(mode="rb") as f:
        blob: commands.Blob = pickle.load(f)
    with temp_file1.open() as f:
        contents = f.read()

    assert blob.name == "a.in"
    assert blob.contents == contents


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
        datetime.min, "initial commit", None
    )


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


def test_commit_empty_message(repo: commands.Repository, temp_file1) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)
    with pytest.raises(
        errors.PyGitletException, match=r"Please enter a commit message\."
    ):
        commands.commit(repo, "")

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


def test_successful_init(repo: commands.Repository) -> None:
    commands.init(repo)


def test_unsuccessful_init(repo: commands.Repository) -> None:
    repo.gitlet.mkdir(exist_ok=True)
    with pytest.raises(
        errors.PyGitletException,
        match=r"A Gitlet version-control system already exists in the current directory\.",
    ):
        commands.init(repo)


def test_add(repo: commands.Repository, temp_file1: Path) -> None:
    commands.init(repo)
    commands.add(repo, temp_file1)

    assert (repo.stage / "a.in").exists()


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

    commands.commit(repo, "commit a.in")
    assert len(list(repo.commits.iterdir())) == 2
    assert len(list(repo.blobs.iterdir())) == 1
    assert len(list(repo.stage.iterdir())) == 0


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

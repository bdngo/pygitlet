from pathlib import Path

import pytest

from pygitlet import commands, errors


@pytest.fixture
def repo(tmp_path: Path) -> commands.Repository:
    return commands.Repository(gitlet=tmp_path / ".gitlet")


def test_successful_init(repo: commands.Repository) -> None:
    print(repo.gitlet)
    commands.init(repo)


def test_unsuccessful_init(repo: commands.Repository) -> None:
    repo.gitlet.mkdir(exist_ok=True)
    with pytest.raises(errors.PyGitletException):
        commands.init(repo)

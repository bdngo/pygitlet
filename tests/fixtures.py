"""Unit tests for PyGitlet."""

import re
from pathlib import Path

import pytest

from pygitlet import commands


@pytest.fixture
def repo(tmp_path: Path) -> commands.Repository:
    return commands.Repository(tmp_path / ".gitlet")


@pytest.fixture
def tmp_file1(tmp_path: Path) -> Path:
    (tmp_path / "a.in").write_text("a\n")
    return Path("a.in")


@pytest.fixture
def tmp_file2(tmp_path: Path) -> Path:
    (tmp_path / "b.in").write_text("b\n")
    return Path("b.in")


@pytest.fixture
def repo_commit_tmp_file1(
    repo: commands.Repository, tmp_file1: Path
) -> commands.Repository:
    commands.init(repo)
    commands.add(repo, tmp_file1)
    commands.commit(repo, "commit a.in")
    return repo


@pytest.fixture
def log_pattern() -> re.Pattern:
    return re.compile(r"(===\ncommit [0-9a-f]+\nDate: .+\n.+)+")


@pytest.fixture
def merge_log_pattern() -> re.Pattern:
    return re.compile(
        r"===\ncommit [0-9a-f]+\nMerge: [0-9a-f]{7} [0-9a-f]{7}\nDate: .+\n.+"
    )


@pytest.fixture
def repo_remote(tmp_path: Path) -> commands.Repository:
    remote_path = tmp_path / "remote"
    remote_path.mkdir(parents=True)
    return commands.Repository(remote_path / ".gitlet")
"""Main PyGitlet CLI."""

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from pygitlet import commands, errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A minimal Git client written in Python",
    )
    subparsers = parser.add_subparsers(
        title="subcommands",
        description="valid subcommands",
        dest="subcommand",
        required=True,
    )

    subparsers.add_parser("init", description="Initialize a PyGitlet repository")

    parser_add = subparsers.add_parser("add", description="Stage files")
    parser_add.add_argument("file", type=Path)

    parser_commit = subparsers.add_parser("commit", description="Commit staged files")
    parser_commit.add_argument("message")

    parser_remove = subparsers.add_parser(
        "rm", description="Remove staged or committed files"
    )
    parser_remove.add_argument("file")

    subparsers.add_parser("log", description="Log of current head commit")
    subparsers.add_parser("global-log", description="Log of all commits")
    subparsers.add_parser("status", description="Status of repository")

    parser_checkout = subparsers.add_parser(
        "checkout", description="Checkout files, commits, or branches"
    )
    parser_checkout.add_argument("checkout_args", nargs=argparse.REMAINDER)

    parser_branch = subparsers.add_parser("branch", description="Create a new branch")
    parser_branch.add_argument("branch")
    parser_remove_branch = subparsers.add_parser(
        "rm-branch", description="Remove a branch"
    )
    parser_remove_branch.add_argument("branch")

    parser_reset = subparsers.add_parser(
        "reset", description="Reset the working directory to a commit"
    )
    parser_reset.add_argument("commit")

    parser_merge = subparsers.add_parser(
        "merge", description="Merge the given branch into the current branch"
    )
    parser_merge.add_argument("branch")

    parser_add_remote = subparsers.add_parser(
        "add-remote", description="Add remote to repository"
    )
    parser_add_remote.add_argument("remote_name")
    parser_add_remote.add_argument(
        "remote_path", type=lambda p: commands.Repository(Path(p))
    )

    parser_remove_remote = subparsers.add_parser(
        "remove-remote", description="Remove remote from repository"
    )
    parser_remove_remote.add_argument("remote_name")

    parser_push = subparsers.add_parser("push", description="Push changes to remote")
    parser_push.add_argument("remote_name")
    parser_push.add_argument("branch_name")

    parser_fetch = subparsers.add_parser(
        "fetch", description="Fetch changes from remote"
    )
    parser_fetch.add_argument("remote_name")
    parser_fetch.add_argument("branch_name")

    parser_pull = subparsers.add_parser("pull", description="Pull changes from remote")
    parser_pull.add_argument("remote_name")
    parser_pull.add_argument("branch_name")

    args = parser.parse_args()
    repo = commands.Repository(Path.cwd() / ".gitlet")
    try:
        if args.subcommand != "init" and not repo.gitlet.exists():
            raise errors.PyGitletException("Not in an initialized Gitlet directory.")
        elif args.subcommand == "init":
            commands.init(repo)
        else:
            engine = sa.create_engine("sqlite+pysqlite:///.gitlet/db.sqlite3")
            with Session(engine) as session:
                match args.subcommand:
                    case "add":
                        commands.add(session, args.file)
                    case "commit":
                        commands.commit(session, args.message)
                    case "rm":
                        commands.remove(session, args.file)
                    case "log":
                        print(commands.log(session))
                    case "global-log":
                        print(commands.global_log(session))
                    case "status":
                        print(commands.status(session))
                    case "checkout":
                        match args.checkout_args:
                            case ["--", file]:
                                commands.checkout_file(repo, file)
                            case [commit_id, file]:
                                commands.checkout_commit(repo, commit_id, file)
                            case [branch]:
                                commands.checkout_branch(repo, branch)
                            case _:
                                raise errors.PyGitletException(
                                    "Unreachable checkout syntax"
                                )
                    case "branch":
                        commands.branch(repo, args.branch)
                    case "rm-branch":
                        commands.remove_branch(repo, args.branch)
                    case "reset":
                        commands.reset(repo, args.commit)
                    case "merge":
                        commands.merge(repo, args.branch)
                    case "add-remote":
                        commands.add_remote(repo, args.remote_name, args.remote_path)
                    case "rm-remote":
                        commands.remove_remote(repo, args.remote_name)
                    case "push":
                        commands.push(repo, args.remote_name, args.branch_name)
                    case "fetch":
                        commands.fetch(repo, args.remote_name, args.branch_name)
                    case "pull":
                        commands.pull(repo, args.remote_name, args.branch_name)
                    case _:
                        raise errors.PyGitletException(
                            "No command with that name exists."
                        )
                session.commit()
    except errors.PyGitletException as e:
        print(e.message, file=sys.stderr)


if __name__ == "__main__":
    main()

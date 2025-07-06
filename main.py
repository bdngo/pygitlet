import argparse
import sys
from pathlib import Path

from pygitlet import commands, errors


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="PyGitlet",
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
    parser_remove.add_argument("file", type=Path)

    subparsers.add_parser("log", description="Log of current head commit")
    subparsers.add_parser("global-log", description="Log of all commits")
    subparsers.add_parser("status", description="Status of repository")

    parser_checkout = subparsers.add_parser(
        "checkout", description="Checkout files, commits, or branches"
    )
    parser_checkout.add_argument("target", nargs="?")
    parser_checkout.add_argument("file")

    args = parser.parse_args()
    repo = commands.Repository(Path.cwd() / ".gitlet")
    try:
        match args.subcommand:
            case "init":
                commands.init(repo)
            case "add":
                commands.add(repo, args.file)
            case "commit":
                commands.commit(repo, args.message)
            case "rm":
                commands.remove(repo, args.file)
            case "log":
                print(commands.log(repo))
            case "global-log":
                print(commands.global_log(repo))
            case "status":
                print(commands.status(repo))
            case "checkout":
                match [args.target, args.files]:
                    case [None, file]:
                        commands.checkout_file(repo, file)
                    case [commit_id, file]:
                        commands.checkout_commit(repo, commit_id, file)
                    case [branch, None]:
                        commands.checkout_branch(repo, branch)
                    case _:
                        raise errors.PyGitletException("Unreachable checkout syntax")
            case _:
                raise errors.PyGitletException("No command with that name exists.")
    except errors.PyGitletException as e:
        print(e.message, file=sys.stderr)


if __name__ == "__main__":
    main()

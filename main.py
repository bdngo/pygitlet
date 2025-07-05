import argparse
import sys

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
    parser_add.add_argument("file")

    parser_add = subparsers.add_parser("commit", description="Commit staged files")
    parser_add.add_argument("message")

    args = parser.parse_args()
    repo = commands.Repository()
    try:
        match args.subcommand:
            case "init":
                commands.init(repo)
            case "add":
                commands.add(repo, args.file)
            case "commit":
                commands.commit(repo, args.message)
            case _:
                raise errors.PyGitletException("No command with that name exists.")
    except errors.PyGitletException as e:
        print(e.message, file=sys.stderr)


if __name__ == "__main__":
    main()

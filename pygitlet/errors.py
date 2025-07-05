"""Custom errors for PyGitlet."""


class PyGitletException(Exception):
    """Exception for any PyGitlet errors."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

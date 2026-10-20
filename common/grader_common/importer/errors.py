"""The one error type the importer raises for anything wrong with an uploaded problem."""


class ProblemImportError(Exception):
    """The upload can't be imported. The message says why, in words an admin can act on."""

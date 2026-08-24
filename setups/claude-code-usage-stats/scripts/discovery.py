# scripts/discovery.py
"""Locate Claude Code account config directories on disk."""
from collections import namedtuple
from pathlib import Path

Account = namedtuple("Account", "name path is_canonical history_path projects_dir")

# Directories that live inside the account store but are not accounts.
_NOT_ACCOUNTS = {"bin"}


def _build(name, path, is_canonical):
    """Return an Account if `path` looks like an account dir, else None."""
    history = path / "history.jsonl"
    projects = path / "projects"
    has_history = history.is_file()
    has_projects = projects.is_dir()
    if not (has_history or has_projects):
        return None
    return Account(
        name=name,
        path=path,
        is_canonical=is_canonical,
        history_path=history if has_history else None,
        projects_dir=projects if has_projects else None,
    )


def discover_accounts(store, canonical):
    """Accounts under `store`, sorted by name, with `canonical` appended last.

    A directory counts as an account only if it holds history.jsonl or a
    projects/ dir -- this is what keeps `store/bin` from registering.
    """
    store, canonical = Path(store), Path(canonical)
    found = []
    if store.is_dir():
        for child in sorted(store.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name in _NOT_ACCOUNTS or child.name.startswith("."):
                continue
            account = _build(child.name, child, False)
            if account:
                found.append(account)
    canonical_account = _build("canonical", canonical, True)
    if canonical_account:
        found.append(canonical_account)
    return found

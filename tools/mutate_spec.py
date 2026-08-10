"""How a mutation is described: from a JSON spec, or from CLI arguments.

Split out of ``tools/mutate.py`` (see its module docstring for ``--spec`` and
``--file``/``--old``/``--new``) because the file grew past a comfortable size
once a mutation could carry several edits. Both entry points here end at the
same place: one or more ``Mutation`` objects, ready for the verdict path or a
prepared tree.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mutate_edits import Edit, HarnessError, Mutation, _repository_relative


def _load_spec(path: Path) -> tuple[Mutation, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise HarnessError(f"{path}: a spec is a JSON list of mutation objects")
    mutations: list[Mutation] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise HarnessError(f"{path}: entry {index} is not an object")
        try:
            label = str(entry["label"])
            raw_edits = entry.get("edits")
            if raw_edits is None:
                raw_edits = [{"file": entry["file"], "old": entry["old"], "new": entry["new"]}]
            elif any(key in entry for key in ("file", "old", "new")):
                raise HarnessError(
                    f"{path}: {label}: `edits` and `file`/`old`/`new` are mutually exclusive; "
                    "the latter would be silently ignored"
                )
            if not isinstance(raw_edits, list) or not raw_edits:
                raise HarnessError(f"{path}: {label}: `edits` is a non-empty list of edit objects")
            for item in raw_edits:
                if not isinstance(item, dict):
                    raise HarnessError(f"{path}: {label}: every entry in `edits` must be an object")
            parsed = tuple(
                Edit(
                    path=_repository_relative(str(item["file"]), label),
                    old=str(item["old"]),
                    new=str(item["new"]),
                )
                for item in raw_edits
            )
            mutations.append(
                Mutation(
                    label=label,
                    path=parsed[0].path,
                    old=parsed[0].old,
                    new=parsed[0].new,
                    also=parsed[1:],
                )
            )
        except KeyError as missing:
            raise HarnessError(f"{path}: entry {index} is missing {missing}") from missing
    if not mutations:
        raise HarnessError(f"{path}: no mutations")
    return tuple(mutations)


def _mutations_from(args: argparse.Namespace) -> tuple[Mutation, ...]:
    if args.spec:
        return _load_spec(Path(args.spec))
    if not args.file:
        raise HarnessError("give either --spec or --file with --old/--new")
    old = Path(args.old_file).read_text(encoding="utf-8") if args.old_file else args.old
    new = Path(args.new_file).read_text(encoding="utf-8") if args.new_file else args.new
    if old is None or new is None:
        raise HarnessError("--file needs --old/--new or --old-file/--new-file")
    label = str(args.label or Path(args.file).name)
    relative = _repository_relative(str(args.file), label)
    return (Mutation(label=label, path=relative, old=str(old), new=str(new)),)

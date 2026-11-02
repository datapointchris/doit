"""`doit import`: hand this desk's config and state files to the service, which merges them.

The service decides what each file is and what is new in it, so a second run adds
nothing and this side never parses a file. Every file in the two folders goes up,
and the report names each one the service skipped and why.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.text import Text

from doit import paths
from doit import service
from doit.render import console
from doit.render import error_console

# This CLI's own login settings, which are not doit's data.
NOT_DATA = frozenset({'config.toml'})


def desk_files(config_dir: Path, state_dir: Path) -> list[dict]:
    """Each file under the two folders as the service takes it, `config/` or `state/` before its name."""
    found = []
    for prefix, folder in (('config', config_dir), ('state', state_dir)):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith('.') or path.name in NOT_DATA:
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            modified = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC)
            found.append({'path': f'{prefix}/{path.name}', 'text': text, 'modified_at': modified.isoformat()})
    return found


def report_lines(summary: dict) -> list[Text]:
    """What the import merged, one line per kind of record."""
    lines = []
    for name, outcome in sorted((summary.get('documents') or {}).items()):
        if 'kept' in outcome:
            lines.append(Text(f'  {name}: kept, {outcome["kept"]}', style='yellow'))
        else:
            lines.append(Text(f'  {name}: {"changed" if outcome.get("changed") else "unchanged"}'))
    journal = summary.get('journal') or {}
    lines.append(Text(f'  journal: {journal.get("added", 0)} added of {journal.get("read", 0)} read'))
    forecasts = summary.get('forecasts') or {}
    lines.append(Text(f'  forecasts: {forecasts.get("added", 0)} added of {forecasts.get("read", 0)} read'))
    review = summary.get('review') or {}
    lines.append(Text(f'  review completions: {review.get("added", 0)} added of {review.get("read", 0)} read'))
    lines.append(Text(f'  offered counts raised: {(summary.get("offers") or {}).get("raised", 0)}'))
    for kind in ('journal', 'forecasts'):
        malformed = (summary.get(kind) or {}).get('malformed', 0)
        if malformed:
            lines.append(Text(f'  {kind}: {malformed} malformed line(s) not read', style='yellow'))
    for rewritten in summary.get('rewritten') or []:
        lines.append(Text(f'  {rewritten["pursuit"]}.{rewritten["field"]} rewritten as a read: {rewritten["command"]}', style='dim'))
    for untranslated in summary.get('untranslated') or []:
        line = f'  {untranslated["pursuit"]}.{untranslated["field"]} left as a command the service cannot run: {untranslated["command"]}'
        lines.append(Text(line, style='yellow'))
    for skipped in summary.get('skipped') or []:
        lines.append(Text(f'  skipped {skipped.get("path")}: {skipped.get("reason")}', style='dim'))
    return lines


def cmd_import(config_dir: Path, state_dir: Path, as_json: bool) -> int:
    files = desk_files(config_dir, state_dir)
    if not files:
        error_console.print(f'Nothing to import: no files in {config_dir} or {state_dir}.')
        return 1
    summary = service.client().post('/imports', {'files': files}, timeout=service.IMPORT_TIMEOUT)
    if as_json:
        print(json.dumps(summary, indent=2))
        return 0
    console.print(f'Imported {len(files)} files from {config_dir} and {state_dir}.')
    for line in report_lines(summary):
        console.print(line)
    return 0


def import_command(
    config_dir: Annotated[
        Path | None,
        typer.Option('--config-dir', help='The folder holding pursuits.yml, register.yml and sources.yml.', show_default='~/.config/doit'),
    ] = None,
    state_dir: Annotated[
        Path | None,
        typer.Option('--state-dir', help='The folder holding the journal, forecasts and review state.', show_default='~/.local/state/doit'),
    ] = None,
    as_json: Annotated[bool, typer.Option('--json', help='Output as JSON to stdout.')] = False,
) -> None:
    """Merge this desk's files into the service. Safe to run again: a second run adds only what is new."""
    configs = config_dir or paths.xdg_config_home() / 'doit'
    states = state_dir or paths.xdg_state_home() / 'doit'
    raise typer.Exit(cmd_import(configs, states, as_json))

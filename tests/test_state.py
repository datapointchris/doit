"""Tests for doit.state — the atomic last-done JSON state file shared by review
and labs."""

from doit import state


def test_load_state_missing_returns_empty(tmp_path):
    assert state.load_state(tmp_path / 'nope.json') == {}


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / 'labs-state.json'
    state.save_state(path, {'fd-find-files': '2026-07-17'})
    assert state.load_state(path) == {'fd-find-files': '2026-07-17'}


def test_save_state_format_is_pretty_json_with_newline(tmp_path):
    # Byte-for-byte format matters: review-state.json is synced data, so the
    # on-disk shape has to survive the move out of dotfiles unchanged.
    path = tmp_path / 's.json'
    state.save_state(path, {'a': '2026-01-01'})
    assert path.read_text() == '{\n  "a": "2026-01-01"\n}\n'


def test_save_state_overwrites(tmp_path):
    path = tmp_path / 's.json'
    state.save_state(path, {'a': '2026-01-01'})
    state.save_state(path, {'a': '2026-02-02', 'b': '2026-03-03'})
    assert state.load_state(path) == {'a': '2026-02-02', 'b': '2026-03-03'}


def test_save_state_leaves_no_temp_file(tmp_path):
    # The atomic write renames a .tmp into place; it must not linger.
    path = tmp_path / 's.json'
    state.save_state(path, {'a': '2026-01-01'})
    assert not (tmp_path / 's.json.tmp').exists()
    assert list(tmp_path.iterdir()) == [path]

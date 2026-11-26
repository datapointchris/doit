"""Which pursuits the desk panel lists choices under."""

from doit import desk_panel

RESOLVE = {'app': 'life', 'path': '/tasks'}


def test_the_panel_lists_choices_that_a_pick_completes_or_that_choices_shown_asks_for():
    assert desk_panel.lists_choices({'resolve': RESOLVE, 'on_log': 'life tasks complete {id}'})
    assert desk_panel.lists_choices({'resolve': RESOLVE, 'choices_shown': 3}), 'a course lists its next chapters as text'
    assert not desk_panel.lists_choices({'resolve': RESOLVE}), 'a resolve kept only to place doit next lists nothing'

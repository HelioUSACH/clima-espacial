from core.management.commands.check_static_comments import find_premature_closes


def test_single_line_comment_is_fine(tmp_path):
    f = tmp_path / "ok.css"
    f.write_text("/* a one-line comment */\n.foo { color: red; }\n")
    assert find_premature_closes([f]) == []


def test_properly_closed_multiline_comment_is_fine(tmp_path):
    f = tmp_path / "ok.css"
    f.write_text("/* line one\n * line two, closes cleanly */\n.foo { color: red; }\n")
    assert find_premature_closes([f]) == []


def test_premature_close_detected_with_correct_line_number(tmp_path):
    # D-053-style: the v0.2.0 bug was a literal "--chart-*/--status-*" --
    # the "*/" mid-sentence closed the comment three lines early.
    f = tmp_path / "broken.css"
    f.write_text("/* top of comment\n * --chart-*/--status-* more text\n * still meant to be inside */\n:root {}\n")
    failures = find_premature_closes([f])
    assert failures == [(f, 1)]


def test_code_immediately_after_a_clean_close_is_not_flagged(tmp_path):
    # A multi-line comment immediately followed by a selector on the same
    # closing line is ordinary, valid style -- not a bug.
    f = tmp_path / "ok.css"
    f.write_text("/* line one\n * line two */ .foo { color: red; }\n")
    assert find_premature_closes([f]) == []


def test_js_block_comment_also_checked(tmp_path):
    f = tmp_path / "broken.js"
    f.write_text("/* setup\n * a path like web/static/foo/*/bar.js\n * more detail */\nconsole.log(1);\n")
    failures = find_premature_closes([f])
    assert failures == [(f, 1)]

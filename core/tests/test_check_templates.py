from core.management.commands.check_templates import find_multiline_comments


def test_single_line_comment_is_fine(tmp_path):
    f = tmp_path / "ok.html"
    f.write_text("<p>hi</p>\n{# a single-line comment #}\n<p>bye</p>\n")
    assert find_multiline_comments([f]) == []


def test_multiline_comment_detected_with_correct_line_number(tmp_path):
    f = tmp_path / "broken.html"
    f.write_text("<p>hi</p>\n{# this\nspans two lines #}\n<p>bye</p>\n")
    failures = find_multiline_comments([f])
    assert failures == [(f, 2)]


def test_block_comment_tag_is_not_flagged(tmp_path):
    # {% comment %}...{% endcomment %} is the correct way to span lines —
    # this check only looks at the {# #} shorthand, which can't.
    f = tmp_path / "ok_block.html"
    f.write_text("{% comment %}\nthis is fine\nacross lines\n{% endcomment %}\n")
    assert find_multiline_comments([f]) == []


def test_multiple_files_and_multiple_comments_all_reported(tmp_path):
    f1 = tmp_path / "a.html"
    f1.write_text("{# ok #}\n{# broken\nhere #}\n")
    f2 = tmp_path / "b.html"
    f2.write_text("{# also\nbroken #}\n")
    failures = find_multiline_comments([f1, f2])
    assert failures == [(f1, 2), (f2, 1)]

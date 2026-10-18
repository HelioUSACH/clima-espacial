"""D-077: deploy.sh hands over to the cloned tag's own deploy.sh.

End to end, in a sandbox: `SWX_DEPLOY_SANDBOX` moves every path under
tmp_path, and stub git/rsync/systemctl/curl/... on PATH log what they are
asked to do. The "previous" release's script (the one Victor starts) and the
"cloned" one differ by a marker on a step after the hand-over, so the output
shows whose logic ran.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY_SH = Path(__file__).resolve().parents[2] / "deploy" / "deploy.sh"
STEP = 'echo "==> venv + dependencies"'
TIMERS = ("swx-collect@noaa.kp.timer", "swx-verify.timer")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

STUBS = {
    # clone ... --branch TAG URL DIR copies $FAKE_RELEASES/TAG; -C DIR describe prints its tag.
    "git": r'''
echo "git $*" >> "$STUB_LOG"
if [[ $1 == clone ]]; then
    dir="${*: -1}"; tag=""
    while (( $# )); do [[ $1 == --branch ]] && tag=$2; shift; done
    mkdir -p "$dir" && cp -R "$FAKE_RELEASES/$tag/." "$dir/" && echo "$tag" > "$dir/.tag"
elif [[ $1 == -C ]]; then
    cat "$2/.tag"
fi
''',
    "rsync": r'''
echo "rsync" >> "$STUB_LOG"
args=("$@"); src="${args[-2]}"; dest="${args[-1]}"
mkdir -p "$dest" && cp -R "$src." "$dest"
''',
    "systemctl": r'''
echo "systemctl $*" >> "$STUB_LOG"
[[ $1 == is-active && " $* " == *" ${STUB_DOWN:-none} "* ]] && exit 3
exit 0
''',
    # The smoke test: the home page names a hashed charts.js; serie.json is a 404 (no data yet).
    "curl": r'''
echo "curl" >> "$STUB_LOG"
if [[ " $* " == *" --output "* ]]; then printf 404; else echo '<script src="static/web/js/charts.0123456789ab.js">'; fi
''',
    "install": 'echo "install $*" >> "$STUB_LOG"',
    "runuser": 'echo "runuser $*" >> "$STUB_LOG"',
    "python3": 'echo "python3 $*" >> "$STUB_LOG"',
    "id": "exit 0",
}


def _script(path, *, marker, legacy=False):
    """deploy.sh with a marker on a post-hand-over step. legacy=True strips the
    hand-over block, the shape of a release from before 0.4.1."""
    text = DEPLOY_SH.read_text()
    assert STEP in text
    text = text.replace(STEP, f'echo "==> venv + dependencies [{marker}]"')
    if legacy:
        text, count = re.subn(
            r"^if \[\[ -z \$\{SWX_DEPLOY_REEXEC:-\} \]\]; then\n.*?^\[\[ \$CLONED == .*?\n",
            'WORK="$(mktemp -d)"\ntrap \'rm -rf "$WORK"\' EXIT\n'
            'git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"\n',
            text, flags=re.S | re.M,
        )
        assert count == 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "root"
    (root / "etc/swx").mkdir(parents=True)
    static = root / "static"
    (static / "web/js").mkdir(parents=True)
    (static / "web/js/charts.0123456789ab.js").write_text("")
    (root / "etc/swx/swx.env").write_text(f"STATIC_ROOT={static}\nALLOWED_HOSTS=localhost\n")
    venv = root / "opt/swx/venv/bin"
    venv.mkdir(parents=True)
    for tool in ("python", "pip"):
        (venv / tool).write_text("#!/usr/bin/env bash\nexit 0\n")
        (venv / tool).chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        (bin_dir / name).write_text("#!/usr/bin/env bash\n" + body.lstrip())
        (bin_dir / name).chmod(0o755)
    (tmp_path / "tmp").mkdir()
    env = dict(os.environ)
    for key in ("SWX_DEPLOY_REEXEC", "SWX_DEPLOY_WORK"):
        env.pop(key, None)
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}", TMPDIR=str(tmp_path / "tmp"), STUB_LOG=str(tmp_path / "calls.log"),
        FAKE_RELEASES=str(tmp_path / "releases"), SWX_DEPLOY_SANDBOX=str(root), SWX_DEPLOY_REPO="stub://repo",
    )

    def release(tag, **script_kwargs):
        tree = tmp_path / "releases" / tag
        _script(tree / "deploy/deploy.sh", **script_kwargs)
        (tree / "deploy/systemd").mkdir(parents=True)
        for timer in TIMERS:
            (tree / "deploy/systemd" / timer).write_text("[Install]\nWantedBy=timers.target\n")
        (tree / "deploy/systemd/swx-web.service").write_text("")
        (tree / "deploy/nginx-clima-espacial.conf").write_text("")
        (tree / "requirements.txt").write_text("")

    def run(tag, previous_marker="previous", **extra_env):
        previous = _script(tmp_path / "previous/deploy.sh", marker=previous_marker)
        result = subprocess.run(["bash", str(previous), tag], env={**env, **extra_env},
                                capture_output=True, text=True, timeout=60)
        result.calls = (tmp_path / "calls.log").read_text().splitlines()
        result.leftover = list((tmp_path / "tmp").iterdir())
        return result

    return release, run


def test_the_cloned_tags_script_does_the_install(sandbox):
    release, run = sandbox
    release("v9.9.9", marker="cloned")
    result = run("v9.9.9")
    assert result.returncode == 0, result.stderr
    assert "==> Re-running v9.9.9's own deploy.sh" in result.stdout
    assert result.stdout.count("venv + dependencies [cloned]") == 1
    assert "[previous]" not in result.stdout
    assert sum(c.startswith("git clone") for c in result.calls) == 1
    assert "Deployed v9.9.9." in result.stdout
    assert result.leftover == []


def test_every_timer_is_enabled_and_checked(sandbox):
    release, run = sandbox
    release("v9.9.9", marker="cloned")
    result = run("v9.9.9")
    assert "systemctl enable --quiet --now swx-web" in result.calls
    assert f"systemctl enable --quiet --now {' '.join(TIMERS)}" in result.calls
    for timer in TIMERS:
        assert f"systemctl is-active --quiet {timer}" in result.calls
    assert "systemctl list-timers swx-* --no-pager" in result.calls


def test_a_timer_that_is_not_active_fails_the_deploy(sandbox):
    release, run = sandbox
    release("v9.9.9", marker="cloned")
    result = run("v9.9.9", STUB_DOWN="swx-verify.timer")
    assert result.returncode != 0
    assert "Timers not enabled and active: swx-verify.timer" in result.stderr
    assert "Deployed v9.9.9." not in result.stdout
    assert result.leftover == []


def test_rollback_to_a_script_without_the_hand_over(sandbox):
    release, run = sandbox
    release("v0.4.0", marker="old", legacy=True)
    result = run("v0.4.0")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("venv + dependencies [old]") == 1
    assert "[previous]" not in result.stdout
    # The old script ignores SWX_DEPLOY_WORK and clones again; both temp dirs go.
    assert sum(c.startswith("git clone") for c in result.calls) == 2
    assert result.leftover == []


def test_the_hand_over_refuses_a_clone_at_another_tag(sandbox, tmp_path):
    release, _ = sandbox
    release("v9.9.9", marker="cloned")
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src/.tag").write_text("v9.9.8\n")
    run_env = dict(os.environ)
    result = subprocess.run(
        ["bash", str(tmp_path / "releases/v9.9.9/deploy/deploy.sh"), "v9.9.9"],
        env={**run_env, "PATH": f"{tmp_path / 'bin'}:{run_env['PATH']}", "STUB_LOG": str(tmp_path / "calls.log"),
             "SWX_DEPLOY_SANDBOX": str(tmp_path / "root"), "SWX_DEPLOY_REEXEC": "1", "SWX_DEPLOY_WORK": str(work)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "not v9.9.9: refusing to deploy it" in result.stderr
    assert "rsync" not in (tmp_path / "calls.log").read_text()


def test_hand_over_comes_before_anything_is_installed():
    lines = DEPLOY_SH.read_text().splitlines()
    first = lambda pattern: next(i for i, line in enumerate(lines) if re.match(pattern, line))  # noqa: E731
    hand_over = first(r'\s*SWX_DEPLOY_REEXEC=1 .*bash "\$WORK/src/deploy/deploy.sh"')
    assert hand_over < first(r"\s*(install|rsync) ")

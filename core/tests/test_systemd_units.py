"""D-070: deploy.sh installs /opt/swx with `--chmod=D755,F644`, so nothing
it ships is executable. A unit whose ExecStart points straight at a repo
`.sh` file fails with 203/EXEC -- swx-dump.service did, and the nightly
backup never ran. Repo scripts must run through an interpreter
(`/bin/bash /opt/swx/...`).

D-077: deploy.sh enables every `*.timer` here, so each one needs an
`[Install]` section with `WantedBy=timers.target` or `enable` does nothing.
"""

import re
from pathlib import Path

import pytest

SYSTEMD = Path(__file__).resolve().parents[2] / "deploy" / "systemd"
UNITS = sorted(SYSTEMD.glob("*.service"))
TIMERS = sorted(SYSTEMD.glob("*.timer"))


def _exec_starts(text):
    # Join systemd's backslash line continuations before reading ExecStart.
    joined = re.sub(r"\\\n", " ", text)
    return [m.group(1).strip() for m in re.finditer(r"^ExecStart=(.+)$", joined, re.M)]


def test_units_found():
    assert UNITS, "no deploy/systemd/*.service files found"
    assert TIMERS, "no deploy/systemd/*.timer files found"


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_execstart_never_execs_a_repo_shell_script_directly(unit):
    for cmd in _exec_starts(unit.read_text()):
        # systemd prefixes (-, @, :, +, !) come before the executable path.
        executable = cmd.split()[0].lstrip("-@:+!")
        assert not (executable.startswith("/opt/swx/") and executable.endswith(".sh")), (
            f"{unit.name}: ExecStart={cmd!r} execs a repo script directly; "
            "deploy.sh installs it mode 644, so use ExecStart=/bin/bash <script>"
        )


def test_check_catches_the_original_bug():
    assert _exec_starts("[Service]\nExecStart=/opt/swx/deploy/swx-dump.sh\n") == ["/opt/swx/deploy/swx-dump.sh"]


def _install_wanted_by(text):
    section = re.search(r"^\[Install\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not section:
        return []
    return " ".join(re.findall(r"^WantedBy=(.+)$", section.group(1), re.M)).split()


@pytest.mark.parametrize("timer", TIMERS, ids=lambda p: p.name)
def test_every_timer_installs_into_timers_target(timer):
    assert "timers.target" in _install_wanted_by(timer.read_text()), (
        f"{timer.name}: needs [Install] WantedBy=timers.target, or deploy.sh's enable does nothing"
    )


def test_wanted_by_check_reads_only_the_install_section():
    assert _install_wanted_by("[Unit]\nWantedBy=timers.target\n[Timer]\nOnCalendar=daily\n") == []
    assert _install_wanted_by("[Timer]\nOnCalendar=daily\n\n[Install]\nWantedBy=timers.target\n") == ["timers.target"]

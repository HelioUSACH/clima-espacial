"""v0.5.1 deploy pieces: the nightly NRT export's unit and schedule (D-091),
the electron collectors' timers (D-092), and the backup prunes that must
never reach nrt/."""

import re
from pathlib import Path

from core.manifests.loader import discover_manifests

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SYSTEMD = DEPLOY / "systemd"


def _directives(unit: str, key: str) -> list[str]:
    return re.findall(rf"^{key}=(.+)$", (SYSTEMD / unit).read_text(), re.M)


def test_export_runs_as_swx_with_the_backup_group_for_this_process_only():
    unit = "swx-export-nrt.service"
    assert _directives(unit, "User") == ["swx"] and _directives(unit, "Group") == ["swx"]
    assert _directives(unit, "SupplementaryGroups") == ["swx-backup"]
    assert _directives(unit, "UMask") == ["0027"]
    assert _directives(unit, "ExecStart") == ["/opt/swx/venv/bin/python manage.py export_nrt"]
    pre = _directives(unit, "ExecStartPre")
    assert "+/usr/bin/install -d -o root -g swx-backup -m 0750 /srv/swx/backups" in pre
    assert "+/usr/bin/install -d -o swx -g swx-backup -m 2750 /srv/swx/backups/nrt" in pre


def test_export_runs_before_the_dump():
    export_at = _directives("swx-export-nrt.timer", "OnCalendar")[0]
    dump_at = _directives("swx-dump.timer", "OnCalendar")[0]
    # Same clock (helio local time, neither says UTC), export first.
    assert "UTC" not in export_at and "UTC" not in dump_at
    assert export_at.split()[1] < dump_at.split()[1]


def test_backup_prunes_stay_at_the_top_level():
    for script, pattern in (("swx-dump.sh", r"^find \"\$BACKUP_DIR\" (.*)-delete$"),
                            ("backup-pull.sh", r"^find \"\$DEST\" (.*)-delete$")):
        finds = re.findall(pattern, (DEPLOY / script).read_text(), re.M)
        assert finds, script
        for args in finds:
            assert "-maxdepth 1" in args, f"{script}: a prune without -maxdepth 1 would reach nrt/"


def test_every_collector_product_has_its_timer():
    for pid, manifest in discover_manifests().items():
        unit = manifest["producer"].get("unit", "")
        if unit.startswith("swx-collect@"):
            assert (SYSTEMD / unit).exists(), f"{pid}: {unit} missing from deploy/systemd"

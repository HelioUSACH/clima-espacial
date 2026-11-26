"""`manage.py check_freshness [--dry-run | --test-email]`: e-mail when a
product goes stale and when it recovers (v0.6.4, D-119; web/freshness.py).
Run every 5 minutes by swx-freshness.timer.

--dry-run   prints each product's state and the mail it would send now,
            without sending or recording anything.
--test-email sends one test mail to SWX_ALERT_EMAILS and says how it went.
"""

from django.core.management.base import BaseCommand

from web import freshness


class Command(BaseCommand):
    help = "E-mail SWX_ALERT_EMAILS when a product turns stale or recovers (D-119)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--dry-run", action="store_true", help="Print what would be sent; change nothing.")
        group.add_argument("--test-email", action="store_true", help="Send one test e-mail to SWX_ALERT_EMAILS.")

    def handle(self, *args, dry_run=False, test_email=False, **options):
        off = freshness.mail_off_reason()
        if test_email:
            if off:
                self.stderr.write(f"Not sent: {off}.")
                return
            result = freshness.send_test()
            to = ", ".join(freshness.settings.SWX_ALERT_EMAILS)
            if result == freshness.SENT:
                self.stdout.write(f"Test e-mail sent to {to}.")
            else:
                self.stderr.write(f"Test e-mail to {to} failed (see the log above).")
            return
        outcomes = freshness.run(dry_run=dry_run)
        if not dry_run:
            for o in outcomes:
                if o.action == "mail":
                    self.stdout.write(f"{o.check.product_id}: {o.kind} mail {o.result}")
            return
        self.stdout.write(f"Dry run. E-mail: {'off, ' + off if off else 'on'}; to: "
                          f"{', '.join(freshness.settings.SWX_ALERT_EMAILS) or '—'}")
        for o in outcomes:
            what = {"first": "first check: state recorded, no mail",
                    "mail": f"would send the {o.kind} mail",
                    "wait": f"{o.kind} mail waits (one per hour)",
                    "": "nothing to send"}[o.action]
            self.stdout.write(f"{o.check.product_id}: {o.check.label}, last datum "
                              f"{freshness.fmt_when(o.check.last_datum)} — {what}")
            if o.action in ("mail", "wait"):
                self.stdout.write(f"  Subject: {o.subject}")
                self.stdout.write("".join(f"  | {line}\n" for line in o.body.splitlines()), ending="")

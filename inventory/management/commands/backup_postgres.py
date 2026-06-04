import os
import subprocess
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a PostgreSQL backup and keep only the latest backups."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backup-dir",
            default=os.getenv("GEARFLOW_BACKUP_DIR"),
            help="Directory for backup files. Defaults to GEARFLOW_BACKUP_DIR.",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=int(os.getenv("GEARFLOW_BACKUP_KEEP", "7")),
            help="Number of latest backup files to keep.",
        )

    def handle(self, *args, **options):
        db_name = os.getenv("POSTGRES_DB")
        if not db_name:
            raise CommandError("POSTGRES_DB is not set; refusing to backup a non-PostgreSQL dev database.")

        backup_dir = options["backup_dir"]
        if not backup_dir:
            raise CommandError("Set GEARFLOW_BACKUP_DIR or pass --backup-dir.")

        keep = options["keep"]
        if keep < 1:
            raise CommandError("--keep must be at least 1.")

        backup_path = Path(backup_dir).expanduser()
        backup_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = backup_path / f"gearflow_{timestamp}.dump"
        command = [
            "pg_dump",
            "--format=custom",
            "--file",
            str(output_file),
            "--dbname",
            db_name,
        ]

        env = os.environ.copy()
        if os.getenv("POSTGRES_HOST"):
            command.extend(["--host", os.getenv("POSTGRES_HOST")])
        if os.getenv("POSTGRES_PORT"):
            command.extend(["--port", os.getenv("POSTGRES_PORT")])
        if os.getenv("POSTGRES_USER"):
            command.extend(["--username", os.getenv("POSTGRES_USER")])
        if os.getenv("POSTGRES_PASSWORD"):
            env["PGPASSWORD"] = os.getenv("POSTGRES_PASSWORD")

        subprocess.run(command, check=True, env=env)

        backups = sorted(backup_path.glob("gearflow_*.dump"), key=lambda path: path.stat().st_mtime, reverse=True)
        for old_backup in backups[keep:]:
            old_backup.unlink()

        self.stdout.write(self.style.SUCCESS(f"Backup created: {output_file}"))

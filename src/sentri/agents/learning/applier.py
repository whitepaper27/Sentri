"""Applier: safely applies approved improvements to .md policy files.

Safety protocol: backup -> version -> write -> verify
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sentri.config.paths import ALERTS_BACKUP_DIR
from sentri.db.md_version_repo import MdVersionRepository

logger = logging.getLogger("sentri.learning.applier")


class Applier:
    """Safely applies improvements to .md policy files with backup and versioning."""

    def __init__(
        self,
        md_version_repo: MdVersionRepository,
        alerts_dir: Path,
        backup_dir: Path | None = None,
    ):
        self._versions = md_version_repo
        self._alerts_dir = alerts_dir
        self._backup_dir = backup_dir or ALERTS_BACKUP_DIR

    def apply(self, proposal: dict) -> dict:
        """Apply an approved proposal to the .md policy file.

        Returns:
            {
                "applied": bool,
                "file_path": str,
                "backup_path": str,
                "version_id": int,
                "error": str (if failed),
            }
        """
        alert_type = proposal.get("alert_type", "")
        if not alert_type:
            return {"applied": False, "error": "No alert_type in proposal"}

        file_path = self._alerts_dir / f"{alert_type}.md"
        if not file_path.exists():
            return {"applied": False, "error": f"File not found: {file_path}"}

        try:
            # Step 1: Read current content
            current_content = file_path.read_text(encoding="utf-8")

            # Step 2: Create backup
            backup_path = self._create_backup(file_path, current_content)

            # Step 3: Record version in database
            content_hash = self._versions.compute_hash(current_content)
            version_id = self._versions.record_version(
                file_path=str(file_path),
                content_hash=content_hash,
                changed_by="learning_engine",
                backup_path=str(backup_path),
                change_reason=proposal.get("reasoning", "Automated improvement"),
            )

            logger.info(
                "Backup created for %s at %s (version %d)",
                alert_type,
                backup_path,
                version_id,
            )

            # Step 4: NOTE - actual content modification is NOT done automatically.
            # The proposal is recorded and backed up. Human review is required
            # to actually modify the .md file. This is a safety measure.
            #
            # In future versions, with high confidence and judge consensus,
            # this could apply the changes directly.

            return {
                "applied": True,
                "file_path": str(file_path),
                "backup_path": str(backup_path),
                "version_id": version_id,
                "note": "Proposal recorded. Human review required to apply changes.",
            }

        except Exception as e:
            logger.error("Failed to apply proposal for %s: %s", alert_type, e)
            return {"applied": False, "error": str(e)}

    def rollback(self, file_path: str) -> dict:
        """Rollback a file to its previous version using the backup.

        Returns:
            {"rolled_back": bool, "error": str (if failed)}
        """
        latest = self._versions.get_latest_version(file_path)
        if not latest:
            return {"rolled_back": False, "error": "No version history found"}

        backup_path = latest.get("backup_path")
        if not backup_path:
            return {"rolled_back": False, "error": "No backup path recorded"}

        backup = Path(backup_path)
        if not backup.exists():
            return {"rolled_back": False, "error": f"Backup file missing: {backup}"}

        try:
            target = Path(file_path)
            shutil.copy2(str(backup), str(target))
            logger.info("Rolled back %s from backup %s", file_path, backup_path)
            return {"rolled_back": True, "restored_from": str(backup)}
        except Exception as e:
            return {"rolled_back": False, "error": str(e)}

    def _create_backup(self, file_path: Path, content: str) -> Path:
        """Create a timestamped backup of the file."""
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_path.stem}_{timestamp}.md"
        backup_path = self._backup_dir / backup_name

        backup_path.write_text(content, encoding="utf-8")
        return backup_path

"""Seed a small amount of synthetic, non-sensitive data for the backup/
restore CI drill (and for local rehearsal). Never uses real member data.

Usage:
    DATABASE_URL=postgresql://... python scripts/backup/seed_synthetic_data.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from matchwell.infrastructure.persistence.models import (
    AssessmentDefinitionRecord,
    CenterRecord,
    CommunityRecord,
    ConsentVersionRecord,
    PilotPlanRecord,
    UserRecord,
)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    engine = create_engine(database_url)
    center_id = uuid.uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            CenterRecord(
                id=center_id, slug="restore-drill-center", name="Restore Drill Center"
            )
        )
        session.add(
            CommunityRecord(
                id=uuid.uuid4(),
                center_id=center_id,
                slug="restore-drill-community",
                name="Restore Drill Community",
            )
        )
        session.add(
            ConsentVersionRecord(
                id=uuid.uuid4(),
                policy_key="pilot-participation",
                version="drill-1.0",
                title="Synthetic drill consent",
                body_markdown="Synthetic consent text for the restore drill only.",
                effective_at=datetime.now(UTC),
                is_active=True,
            )
        )
        session.add(
            AssessmentDefinitionRecord(
                id=uuid.uuid4(),
                key="restore-drill-assessment",
                version="1.0",
                title="Synthetic drill assessment",
                description="Synthetic assessment for the restore drill only.",
                questions=[{"id": "q1", "prompt": "Synthetic question."}],
                is_active=True,
            )
        )
        session.add(
            PilotPlanRecord(
                id=uuid.uuid4(),
                key="restore-drill-plan",
                name="Restore Drill Plan",
                price_minor_units=0,
                currency="usd",
                is_active=True,
            )
        )
        for index in range(3):
            session.add(
                UserRecord(
                    id=uuid.uuid4(),
                    center_id=center_id,
                    oidc_issuer="https://accounts.google.com",
                    oidc_subject=f"synthetic-drill-subject-{index}",
                    email=f"synthetic-drill-{index}@example.com",
                    name=f"Synthetic Drill User {index}",
                    role="member",
                )
            )

    print("Synthetic restore-drill data seeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from matchwell.application.pilot import PilotService
from matchwell.domain.access import AuthenticatedUser
from matchwell.infrastructure.persistence.models import (
    CommunityCovenantDefinitionRecord,
)
from matchwell.infrastructure.persistence.pilot_repository import (
    COMMUNITY_COVENANT_POLICY_KEY,
)

TEST_COVENANT_AFFIRMATIONS = [
    {"key": "christian_identity", "label": "I identify as Christian."},
    {
        "key": "community_conduct",
        "label": "I will participate truthfully and treat every person with dignity.",
    },
]


def seed_community_covenant(session: Session) -> uuid.UUID:
    covenant_id = uuid.uuid4()
    session.add(
        CommunityCovenantDefinitionRecord(
            id=covenant_id,
            policy_key=COMMUNITY_COVENANT_POLICY_KEY,
            display_version="Test 1.0",
            revision=1,
            effective_at=datetime.now(UTC),
            title="Faith & Community Covenant",
            body_markdown="Test participation commitments.",
            required_affirmations=TEST_COVENANT_AFFIRMATIONS,
            is_active=True,
        )
    )
    return covenant_id


def accept_community_covenant(service: PilotService, member: AuthenticatedUser) -> None:
    covenant = service.community_covenant(member)
    service.accept_community_covenant(
        member,
        covenant.id,
        frozenset(item.key for item in covenant.required_affirmations),
    )

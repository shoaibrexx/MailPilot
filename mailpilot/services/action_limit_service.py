from datetime import datetime, timedelta, timezone

from ..extensions import db
from ..models import WorkspaceActionThrottle


class SensitiveActionRateLimited(ValueError):
    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__("This workspace action is temporarily rate limited.")


def reserve_action_cooldown(action_key, cooldown_seconds, *, now=None):
    key = str(action_key or "").strip().casefold()
    seconds = int(cooldown_seconds)
    if not key or len(key) > 80 or seconds < 1:
        raise ValueError("A valid action key and cooldown are required.")
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    db.session.commit()
    if db.engine.dialect.name == "sqlite":
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    record = db.session.get(WorkspaceActionThrottle, key)
    if record is not None and record.next_allowed_at > now:
        retry_after = (record.next_allowed_at - now).total_seconds()
        db.session.rollback()
        raise SensitiveActionRateLimited(retry_after)
    if record is None:
        record = WorkspaceActionThrottle(action_key=key, next_allowed_at=now)
        db.session.add(record)
    record.next_allowed_at = now + timedelta(seconds=seconds)
    record.updated_at = now
    db.session.commit()
    return record.next_allowed_at

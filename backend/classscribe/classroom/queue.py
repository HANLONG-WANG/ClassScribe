"""Database-backed queue controls. Call mutations inside an immediate transaction."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from classscribe.db.models import AppSetting, Job


def paused(session: Session) -> bool:
    setting = session.get(AppSetting, "classroom_queue")
    return bool(setting and setting.value_json.get("paused"))


def set_paused(session: Session, value: bool) -> None:
    setting = session.get(AppSetting, "classroom_queue")
    if setting is None:
        session.add(AppSetting(key="classroom_queue", value_json={"paused": value}))
    else:
        setting.value_json = {"paused": value}


def append(session: Session, job: Job) -> None:
    job.queue_order = int(session.scalar(select(func.max(Job.queue_order))) or 0) + 1

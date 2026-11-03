"""Retenção de feedbacks (ciclo de vida / LGPD).

Config ``FEEDBACK_RETENTION_DAYS`` (0 = desligado), CLI ``flask
purge-old-feedbacks`` (com ``--dry-run``) e o job diário às 03:00, que só é
registrado quando a retenção está ligada. Nada chama a rede.
"""
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app import create_app, db
from app import scheduler as app_scheduler
from app.config import Config
from app.models.feedback import Feedback, RequestedFeature
from app.scheduling import (
    RETENTION_JOB_ID,
    run_retention_purge,
    schedule_retention_purge,
)
from app.services.feedback_service import FeedbackService


def _add(days_ago, *, features=()):
    feedback = Feedback(
        feedback_text="texto",
        sentiment="POSITIVO",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    for code, reason in features:
        feedback.requested_features.append(RequestedFeature(code=code, reason=reason))
    db.session.add(feedback)
    return feedback


# --------------------------------------------------------------- service

def test_purge_older_than_deletes_only_the_old_and_their_features(app):
    _add(40, features=[("ANTIGO", "r")])
    _add(10)
    _add(1)
    db.session.commit()

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    deleted = FeedbackService.purge_older_than(cutoff)

    assert deleted == 1
    assert db.session.query(Feedback).count() == 2
    # A feature do feedback apagado some junto.
    assert db.session.query(RequestedFeature).count() == 0


def test_count_older_than_does_not_delete(app):
    _add(40)
    _add(5)
    db.session.commit()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    assert FeedbackService.count_older_than(cutoff) == 1
    assert db.session.query(Feedback).count() == 2


# --------------------------------------------------------------- CLI

def test_cli_dry_run_counts_without_deleting(app):
    _add(40)
    _add(2)
    db.session.commit()
    runner = app.test_cli_runner()

    result = runner.invoke(args=["purge-old-feedbacks", "--days", "30", "--dry-run"])

    assert result.exit_code == 0
    assert "1 feedback" in result.output
    assert "dry-run" in result.output
    assert db.session.query(Feedback).count() == 2


def test_cli_purges_with_explicit_days(app):
    _add(40)
    _add(2)
    db.session.commit()
    runner = app.test_cli_runner()

    result = runner.invoke(args=["purge-old-feedbacks", "--days", "30"])

    assert result.exit_code == 0
    assert db.session.query(Feedback).count() == 1


def test_cli_uses_config_retention_when_days_omitted(app):
    _add(40)
    _add(2)
    db.session.commit()
    app.config["FEEDBACK_RETENTION_DAYS"] = 30
    runner = app.test_cli_runner()

    result = runner.invoke(args=["purge-old-feedbacks"])

    assert result.exit_code == 0
    assert db.session.query(Feedback).count() == 1


def test_cli_does_nothing_when_retention_disabled(app):
    _add(40)
    db.session.commit()
    app.config["FEEDBACK_RETENTION_DAYS"] = 0
    runner = app.test_cli_runner()

    result = runner.invoke(args=["purge-old-feedbacks"])

    assert result.exit_code == 0
    assert "desligada" in result.output
    assert db.session.query(Feedback).count() == 1


# --------------------------------------------------------------- job

def test_run_retention_purge_respects_config(app):
    _add(40)
    _add(2)
    db.session.commit()
    app.config["FEEDBACK_RETENTION_DAYS"] = 30

    deleted = run_retention_purge(app)

    assert deleted == 1
    assert db.session.query(Feedback).count() == 1


def test_run_retention_purge_is_noop_when_disabled(app):
    _add(40)
    db.session.commit()
    app.config["FEEDBACK_RETENTION_DAYS"] = 0

    assert run_retention_purge(app) == 0
    assert db.session.query(Feedback).count() == 1


def test_schedule_registers_daily_3am_job_only_when_retention_on(app):
    app.config["FEEDBACK_RETENTION_DAYS"] = 7
    app.config["SCHEDULER_TIMEZONE"] = "America/Sao_Paulo"
    scheduler = BackgroundScheduler()
    schedule_retention_purge(scheduler, app)

    job = scheduler.get_job(RETENTION_JOB_ID)
    assert job is not None
    assert job.args == (app,)
    assert str(job.trigger.timezone) == "America/Sao_Paulo"
    assert "hour='3'" in str(job.trigger)
    assert "day='1'" not in str(job.trigger)


def test_schedule_skips_job_when_retention_off(app):
    app.config["FEEDBACK_RETENTION_DAYS"] = 0
    scheduler = BackgroundScheduler()
    schedule_retention_purge(scheduler, app)

    assert scheduler.get_job(RETENTION_JOB_ID) is None


def test_in_app_scheduler_registers_retention_job_when_on_and_enabled():
    enabled = type("RetentionEnabled", (Config,), {
        "SCHEDULER_ENABLED": True,
        "FEEDBACK_RETENTION_DAYS": 7,
    })
    create_app(enabled)
    try:
        assert app_scheduler.running
        assert app_scheduler.get_job(RETENTION_JOB_ID) is not None
    finally:
        app_scheduler.remove_all_jobs()
        app_scheduler.shutdown(wait=False)


def test_in_app_scheduler_skips_retention_job_when_off():
    enabled = type("RetentionOff", (Config,), {
        "SCHEDULER_ENABLED": True,
        "FEEDBACK_RETENTION_DAYS": 0,
    })
    create_app(enabled)
    try:
        assert app_scheduler.running
        assert app_scheduler.get_job(RETENTION_JOB_ID) is None
    finally:
        app_scheduler.remove_all_jobs()
        app_scheduler.shutdown(wait=False)

"""Testes do resumo semanal: job com app context, agregação, e-mail e SMTP.

O LLM do e-mail é substituído pelo template estático, então nada sai para a
rede; o SMTP é mockado.
"""
import runpy
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app import db, scheduler as app_scheduler, validate_config
from app.config import Config
from app.models.feedback import (
    ANALYSIS_ANALYZED,
    ANALYSIS_UNANALYZED,
    Feedback,
    RequestedFeature,
)
from app.scheduling import JOB_ID, run_weekly_summary, schedule_weekly_summary
from app.services import email_service

EMAIL_SETTINGS = {
    "EMAIL_HOST": "smtp.example.com",
    "EMAIL_PORT": 587,
    "EMAIL_USER": "bot@example.com",
    "EMAIL_PASSWORD": "senha",
    "EMAIL_RECIPIENTS": ["time@example.com"],
}
SMTP = "app.services.email_service.smtplib.SMTP"
LLM_EMAIL = "app.services.email_service.generate_email_content_llm"


@pytest.fixture()
def email_config():
    with patch.multiple(Config, **EMAIL_SETTINGS):
        yield


def _add_feedback(sentiment, *, spam=False, days_ago=1, features=(),
                  analysis_status=ANALYSIS_ANALYZED):
    feedback = Feedback(
        feedback_text="texto",
        sentiment=sentiment,
        is_spam=spam,
        analysis_status=analysis_status,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    for code, reason in features:
        feedback.requested_features.append(RequestedFeature(code=code, reason=reason))
    db.session.add(feedback)


# --- job agendado ----------------------------------------------------------------

def test_job_sends_from_a_thread_without_app_context(app, email_config):
    """A thread do APScheduler não tem app context; o job precisa criar o seu."""
    _add_feedback("POSITIVO", features=[("EDITAR_PERFIL", "quer editar")])
    db.session.commit()
    outcome = {}

    with patch(SMTP) as smtp_cls, patch(LLM_EMAIL, side_effect=email_service.generate_email_content):
        worker = threading.Thread(target=lambda: outcome.update(sent=run_weekly_summary(app)))
        worker.start()
        worker.join(timeout=10)

    assert outcome == {"sent": True}
    smtp_cls.return_value.__enter__.return_value.sendmail.assert_called_once()


def test_scheduler_is_opt_in(app):
    assert app.config["SCHEDULER_ENABLED"] is False
    assert not app_scheduler.running


def test_in_app_scheduler_starts_and_registers_the_job_when_enabled():
    import app as app_package
    from app import create_app

    enabled = type("SchedulerEnabled", (Config,), {"SCHEDULER_ENABLED": True})
    application = create_app(enabled)
    try:
        # O submódulo de agendamento não pode sombrear o atributo app.scheduler.
        assert isinstance(app_package.scheduler, BackgroundScheduler)
        assert app_scheduler.running
        job = app_scheduler.get_job(JOB_ID)
        assert job is not None
        assert job.args == (application,)
    finally:
        app_scheduler.remove_all_jobs()
        app_scheduler.shutdown(wait=False)


def test_schedule_registers_monday_job_in_configured_timezone(app):
    app.config["SCHEDULER_TIMEZONE"] = "America/Sao_Paulo"
    scheduler = BackgroundScheduler()
    schedule_weekly_summary(scheduler, app)

    job = scheduler.get_job(JOB_ID)
    assert job.args == (app,)
    assert str(job.trigger.timezone) == "America/Sao_Paulo"
    assert "day_of_week='mon'" in str(job.trigger)
    assert "hour='8'" in str(job.trigger)


def test_cli_send_weekly_summary_reports_outcome(app):
    runner = app.test_cli_runner()
    target = "app.services.email_service.send_weekly_summary_email"
    with patch(target, return_value=True):
        ok = runner.invoke(args=["send-weekly-summary"])
    with patch(target, return_value=False):
        failed = runner.invoke(args=["send-weekly-summary"])

    assert ok.exit_code == 0
    assert "Resumo semanal enviado" in ok.output
    assert failed.exit_code == 1
    assert "NAO enviado" in failed.output


# --- estatísticas da semana ---------------------------------------------------

def test_weekly_summary_aggregates_window_and_skips_spam(app):
    _add_feedback("POSITIVO", features=[("EDITAR_PERFIL", "editar o perfil"),
                                        ("MODO_ESCURO", "usa a noite")])
    _add_feedback("POSITIVO", features=[("EDITAR_PERFIL", "trocar a foto")])
    _add_feedback("NEGATIVO")
    _add_feedback("NEGATIVO", spam=True, features=[("SPAM_X", "spam")])
    _add_feedback("POSITIVO", days_ago=10, features=[("ANTIGO", "fora da janela")])
    db.session.commit()

    stats = email_service.generate_weekly_summary()

    assert stats["total_feedbacks"] == 3
    assert stats["positive_count"] == 2
    assert stats["negative_count"] == 1
    assert stats["positive_percentage"] == pytest.approx(200 / 3)
    assert [f["code"] for f in stats["top_features"]] == ["EDITAR_PERFIL", "MODO_ESCURO"]
    assert stats["top_features"][0]["count"] == 2
    assert sorted(stats["top_features"][0]["reasons"]) == ["editar o perfil", "trocar a foto"]


def test_weekly_summary_empty_week(app):
    stats = email_service.generate_weekly_summary()
    assert stats["total_feedbacks"] == 0
    assert stats["positive_count"] == 0
    assert stats["top_features"] == []


def test_weekly_summary_skips_unanalyzed_like_the_dashboard(app):
    """Feedbacks que a IA não analisou não entram nos números do resumo.

    O sentimento deles é só o valor padrão (INCONCLUSIVO), não uma opinião; o
    resumo usa o mesmo critério do dashboard e os deixa de fora, em vez de
    contá-los como Inconclusivos.
    """
    _add_feedback("POSITIVO", features=[("EDITAR_PERFIL", "editar o perfil")])
    _add_feedback("NEGATIVO")
    # Não analisados: sentimento só padrão. A feature deles também fica fora.
    _add_feedback("INCONCLUSIVO", analysis_status=ANALYSIS_UNANALYZED,
                  features=[("FANTASMA", "nao analisado")])
    _add_feedback("INCONCLUSIVO", analysis_status=ANALYSIS_UNANALYZED)
    db.session.commit()

    stats = email_service.generate_weekly_summary()

    assert stats["total_feedbacks"] == 2
    assert stats["positive_count"] == 1
    assert stats["negative_count"] == 1
    assert stats["positive_percentage"] == pytest.approx(50.0)
    assert stats["negative_percentage"] == pytest.approx(50.0)
    assert [f["code"] for f in stats["top_features"]] == ["EDITAR_PERFIL"]
    assert "FANTASMA" not in {f["code"] for f in stats["top_features"]}


def test_weekly_summary_keeps_analyzed_inconclusive(app):
    """Um Inconclusivo DE VERDADE (analisado) continua contando.

    A exclusão é por ``analysis_status``, não pelo sentimento: a IA pode ter
    analisado e concluído que o feedback é inconclusivo, e isso é uma opinião.
    """
    _add_feedback("POSITIVO")
    _add_feedback("INCONCLUSIVO", analysis_status=ANALYSIS_ANALYZED)
    db.session.commit()

    stats = email_service.generate_weekly_summary()

    assert stats["total_feedbacks"] == 2
    assert stats["positive_count"] == 1
    assert stats["negative_count"] == 0


def test_weekly_summary_all_unanalyzed_is_an_empty_week(app):
    """Só não analisados = semana vazia, não um e-mail cheio de Inconclusivos."""
    _add_feedback("INCONCLUSIVO", analysis_status=ANALYSIS_UNANALYZED)
    _add_feedback("INCONCLUSIVO", analysis_status=ANALYSIS_UNANALYZED)
    db.session.commit()

    stats = email_service.generate_weekly_summary()

    assert stats["total_feedbacks"] == 0
    assert stats["top_features"] == []


# --- conteúdo do e-mail -------------------------------------------------------

STATS = {
    "total_feedbacks": 4,
    "positive_count": 3,
    "negative_count": 1,
    "positive_percentage": 75.0,
    "negative_percentage": 25.0,
    "top_features": [],
}


def test_static_email_escapes_llm_derived_codes():
    stats = {**STATS, "top_features": [
        {"code": '<img src=x onerror="alert(1)">', "count": 1, "reasons": []}]}
    out = email_service.generate_email_content(stats)
    assert "<img" not in out
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in out


def test_static_email_uses_real_counts():
    out = email_service.generate_email_content(STATS)
    assert "(3 feedbacks)" in out
    assert "(1 feedbacks)" in out


def test_static_email_links_dashboard_only_with_base_url():
    with patch.object(Config, "APP_BASE_URL", ""):
        without_url = email_service.generate_email_content(STATS)
    with patch.object(Config, "APP_BASE_URL", "https://feedlens.example.com"):
        with_url = email_service.generate_email_content(STATS)

    assert "<a " not in without_url
    assert 'href="https://feedlens.example.com/"' in with_url


# --- SMTP ---------------------------------------------------------------------------

def test_smtp_uses_timeout_and_verified_tls(app, email_config):
    with patch(SMTP) as smtp_cls, patch(LLM_EMAIL, return_value="<p>oi</p>"), \
            patch.object(Config, "EMAIL_TIMEOUT", 12):
        assert email_service.send_weekly_summary_email() is True

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=12)
    server = smtp_cls.return_value.__enter__.return_value
    context = server.starttls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    smtp_cls.return_value.__exit__.assert_called_once()


def test_smtp_failure_returns_false_and_still_closes(app, email_config):
    with patch(SMTP) as smtp_cls, patch(LLM_EMAIL, return_value="<p>oi</p>"):
        server = smtp_cls.return_value.__enter__.return_value
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
        assert email_service.send_weekly_summary_email() is False

    smtp_cls.return_value.__exit__.assert_called_once()


# --- boot e servidor ------------------------------------------------------------

def test_production_without_email_config_only_warns():
    prod = type("ProdWithoutEmail", (Config,), {
        "FLASK_ENV": "production",
        "SECRET_KEY": "k" * 48,
        "OPENAI_API_KEY": "test-key",
        "EMAIL_HOST": None,
        "EMAIL_USER": None,
        "EMAIL_PASSWORD": None,
        "EMAIL_RECIPIENTS": [],
    })
    validate_config(prod)


def test_gunicorn_config_is_single_threaded_worker_on_platform_port(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    settings = runpy.run_path(str(Path(__file__).resolve().parents[1] / "gunicorn.conf.py"))
    assert settings["bind"] == "0.0.0.0:10000"
    assert settings["workers"] == 1
    assert settings["worker_class"] == "gthread"
    assert settings["threads"] >= 2

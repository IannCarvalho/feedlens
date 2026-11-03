"""Agendamento do resumo mensal por e-mail.

O resumo sai no dia 1 de cada mês, às 8h (``SCHEDULER_TIMEZONE``), e cobre o
mês civil que acabou de fechar.

Duas regras guiam este módulo:

1. **O job precisa de um app context.** Ele consulta o banco via
   Flask-SQLAlchemy, e a thread do APScheduler não tem contexto nenhum: sem
   ``app.app_context()`` o envio morre com "Working outside of application
   context" todo dia 1.
2. **Um agendador por deploy.** Cada processo que iniciasse o seu (um por
   worker do gunicorn, ou pai e filho do reloader do Flask) mandaria o
   e-mail em dobro. Por isso ele é opt-in (``SCHEDULER_ENABLED``) e há um
   processo dedicado para deploys com mais de um worker.

Formas de rodar:

- dentro do app, com ``SCHEDULER_ENABLED=true``, em deploys de processo
  único (o padrão do container: 1 worker do gunicorn com threads);
- num processo à parte: ``python -m app.scheduling``;
- sob demanda ou por um cron externo: ``flask send-monthly-summary``.
"""
import logging

import click
from apscheduler.schedulers.base import BaseScheduler
from flask import Flask

logger = logging.getLogger(__name__)

JOB_ID = 'monthly_summary_email'


def run_monthly_summary(app: Flask) -> bool:
    """Gera e envia o resumo dentro de um app context próprio.

    Returns:
        True se o e-mail foi enviado.
    """
    from app.services.email_service import send_monthly_summary_email

    with app.app_context():
        return send_monthly_summary_email()


def schedule_monthly_summary(scheduler: BaseScheduler, app: Flask) -> None:
    """Agenda o resumo para o dia 1 de cada mês às 8h no fuso configurado."""
    timezone = app.config.get('SCHEDULER_TIMEZONE', 'America/Sao_Paulo')
    scheduler.add_job(
        run_monthly_summary,
        'cron',
        args=[app],
        day=1,
        hour=8,
        minute=0,
        timezone=timezone,
        id=JOB_ID,
        replace_existing=True,
        # Se o processo estava fora do ar na hora (o Render free dorme), envia
        # uma vez ao voltar (dentro de 1h), em vez de pular ou acumular disparos.
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info('Monthly summary scheduled for day 1 of every month at 08:00 (%s)', timezone)


def register_cli(app: Flask) -> None:
    """Registra ``flask send-monthly-summary`` (teste manual ou cron externo)."""

    @app.cli.command('send-monthly-summary')
    def send_monthly_summary_command():
        """Gera e envia agora o resumo do mês anterior."""
        if not run_monthly_summary(app):
            raise click.ClickException('Resumo mensal NAO enviado (veja o log).')
        click.echo('Resumo mensal enviado.')


def main() -> None:
    """Processo dedicado ao agendador: ``python -m app.scheduling``."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    from app import create_app
    from app.config import Config

    class DedicatedSchedulerConfig(Config):
        # Este processo JÁ É o agendador: não sobe o BackgroundScheduler do app.
        SCHEDULER_ENABLED = False

    app = create_app(DedicatedSchedulerConfig)
    scheduler = BlockingScheduler()
    schedule_monthly_summary(scheduler, app)
    logger.info('Dedicated scheduler process started')
    scheduler.start()


if __name__ == '__main__':
    main()

"""Comandos de manutenção do cofre de chaves (``flask ...``)."""
import click
from flask import Flask

from app import db
from app.llm.vault import VaultDecryptError, VaultDisabledError


def register_analysis_cli(app: Flask) -> None:
    """Registra ``flask reanalyze-pending``."""

    @app.cli.command('reanalyze-pending')
    @click.option('--limit', type=click.IntRange(min=1), default=None,
                  help='Processa no máximo N feedbacks (do mais antigo ao mais novo).')
    @click.option('--id', 'ids', multiple=True,
                  help='Reanalisa este feedback (UUID), mesmo que já analisado. '
                       'Pode repetir.')
    @click.option('--stop-after', type=click.IntRange(min=1), default=3, show_default=True,
                  help='Para depois de N falhas seguidas (ex.: cota esgotada), '
                       'em vez de gastar chamadas à toa.')
    def reanalyze_pending_command(limit, ids, stop_after):
        """Roda a IA de novo nos feedbacks que ela não conseguiu analisar.

        São os marcados como "não analisados" (cota excedida, timeout, resposta
        inválida). Use depois que a cota voltar ou o provedor se recuperar.
        Termina com código 1 se algum continuou sem análise.
        """
        import uuid

        from app.llm import LLMNotConfiguredError
        from app.services.feedback_analyzer import FeedbackAnalyzer
        from app.services.feedback_service import FeedbackService

        if ids:
            feedbacks = []
            for raw in ids:
                try:
                    uuid.UUID(raw)
                except ValueError:
                    raise click.ClickException(
                        f'Id inválido (precisa ser UUID): {raw}') from None
                feedback = FeedbackService.get_feedback_by_id(raw)
                if feedback is None:
                    raise click.ClickException(f'Feedback não encontrado: {raw}')
                feedbacks.append(feedback)
        else:
            feedbacks = FeedbackService.pending_analysis(limit)

        if not feedbacks:
            click.echo('Nenhum feedback aguardando análise.')
            return

        analyzer = FeedbackAnalyzer()
        done = failed = streak = 0
        for feedback in feedbacks:
            try:
                failure = FeedbackService.reanalyze(feedback, analyzer)
            except LLMNotConfiguredError:
                raise click.ClickException(
                    'Nenhum provedor de LLM configurado: defina uma chave no '
                    'servidor ou cadastre uma em Provedores.') from None
            if failure is None:
                done += 1
                streak = 0
                click.echo(f'ok     {feedback.id}  {feedback.sentiment}')
                continue
            failed += 1
            streak += 1
            click.echo(f'falhou {feedback.id}  ({failure})', err=True)
            if streak >= stop_after:
                click.echo(
                    f'Parei depois de {streak} falhas seguidas ({failure}). '
                    'Tente de novo quando o provedor se recuperar.', err=True)
                break

        pending = FeedbackService.count_unanalyzed()
        click.echo(f'{done} reanalisado(s), {failed} falha(s); '
                   f'{pending} ainda aguardam análise.')
        if failed:
            raise click.exceptions.Exit(1)


def register_vault_cli(app: Flask) -> None:
    """Registra ``flask rotate-llm-keys``."""

    @app.cli.command('rotate-llm-keys')
    def rotate_llm_keys_command():
        """Recifra as chaves salvas com a chave PRIMÁRIA (a primeira da lista).

        Rotação sem parar o app: (1) ponha a chave nova ANTES da antiga em
        FEEDLENS_ENCRYPTION_KEY ("nova,antiga") e reinicie; (2) rode este
        comando; (3) remova a antiga da variável.
        """
        from app.models.provider_credential import ProviderCredential
        from app.services.credential_store import get_vault

        vault = get_vault()
        if not vault.enabled:
            raise click.ClickException(str(VaultDisabledError()))
        rows = db.session.query(ProviderCredential).all()
        failed = 0
        for row in rows:
            try:
                row.encrypted_key = vault.rotate(row.encrypted_key)
            except VaultDecryptError:
                failed += 1
                click.echo(f'NAO recifrada: {row.provider}/{row.model} ({row.id})', err=True)
        db.session.commit()
        click.echo(f'{len(rows) - failed} chave(s) recifrada(s).')
        if failed:
            raise click.ClickException(f'{failed} chave(s) não puderam ser decifradas.')

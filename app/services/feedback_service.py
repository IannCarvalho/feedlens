from typing import List, Optional, Dict
from app import db
from app.models.feedback import (
    ANALYSIS_ANALYZED,
    ANALYSIS_UNANALYZED,
    FEATURE_CODE_MAX_LENGTH,
    Feedback,
    RequestedFeature,
)
from sqlalchemy import desc, false, func, true
from sqlalchemy.orm import selectinload

# O que a listagem faz com o que a IA marcou como spam.
SPAM_EXCLUDE = 'exclude'
SPAM_INCLUDE = 'include'
SPAM_ONLY = 'only'
SPAM_MODES = (SPAM_EXCLUDE, SPAM_INCLUDE, SPAM_ONLY)

# Filtro de listagem por estado da análise: tudo (padrão) ou só os que a IA
# não conseguiu analisar.
ANALYSIS_ANY = 'any'
ANALYSIS_FILTERS = (ANALYSIS_ANY, ANALYSIS_UNANALYZED)

# Valores válidos de sentimento (os mesmos que a análise da IA devolve).
SENTIMENTS = ('POSITIVO', 'NEGATIVO', 'INCONCLUSIVO')


class FeedbackService:
    """Service class to handle feedback operations"""

    @staticmethod
    def get_feedback_by_id(feedback_id: str) -> Optional[Feedback]:
        """Get a feedback by its ID"""
        return db.session.get(Feedback, feedback_id)

    @staticmethod
    def get_recent_feedbacks(limit: int = 10) -> List[Feedback]:
        """Get most recent feedbacks"""
        # Forçar exclusão de spam sempre
        return Feedback.query.filter_by(is_spam=False).order_by(desc(Feedback.created_at)).limit(limit).all()

    @staticmethod
    def get_filtered_feedbacks(
        sentiment: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        spam: str = SPAM_EXCLUDE,
        analysis: str = ANALYSIS_ANY,
    ) -> Dict:
        """Get paginated feedbacks with optional filters

        ``spam`` decide o que a IA marcou como ruído: ``exclude`` (padrão, só o
        que não é spam), ``include`` (tudo) ou ``only`` (só os spams, para
        revisão humana). ``analysis`` = ``unanalyzed`` limita aos que a IA não
        conseguiu analisar (cota, timeout...).
        """
        # selectinload: as features da página inteira vêm em UMA query extra
        # (WHERE feedback_id IN ...), em vez de uma query por feedback quando
        # to_dict() ou o template acessam requested_features.
        query = Feedback.query.options(selectinload(Feedback.requested_features))

        if sentiment:
            query = query.filter(Feedback.sentiment == sentiment.upper())

        if spam == SPAM_EXCLUDE:
            query = query.filter(Feedback.is_spam == false())
        elif spam == SPAM_ONLY:
            query = query.filter(Feedback.is_spam == true())
        elif spam != SPAM_INCLUDE:
            raise ValueError(f'spam deve ser um de {SPAM_MODES}, recebeu {spam!r}')

        if analysis == ANALYSIS_UNANALYZED:
            query = query.filter(Feedback.analysis_status == ANALYSIS_UNANALYZED)
        elif analysis != ANALYSIS_ANY:
            raise ValueError(
                f'analysis deve ser um de {ANALYSIS_FILTERS}, recebeu {analysis!r}')

        pagination = query.order_by(
            desc(Feedback.created_at)
        ).paginate(page=page, per_page=per_page)

        return {
            'items': pagination.items,
            'pagination': pagination,
            'sentiment': sentiment
        }

    @staticmethod
    def count_unanalyzed() -> int:
        """Quantos feedbacks (fora spam) a IA ainda não conseguiu analisar."""
        return db.session.query(func.count(Feedback.id)).filter(
            Feedback.analysis_status == ANALYSIS_UNANALYZED,
            Feedback.is_spam == false(),
        ).scalar() or 0

    @staticmethod
    def pending_analysis(limit: Optional[int] = None) -> List[Feedback]:
        """Feedbacks não analisados, do mais antigo ao mais novo."""
        query = Feedback.query.filter(
            Feedback.analysis_status == ANALYSIS_UNANALYZED
        ).order_by(Feedback.created_at)
        return query.limit(limit).all() if limit else query.all()

    @staticmethod
    def apply_analysis(feedback: Feedback, result: Dict) -> bool:
        """Grava no ``feedback`` o resultado do ``FeedbackAnalyzer``.

        Resultado com ``failure`` (a IA não respondeu) mantém o feedback como
        ``unanalyzed`` e registra só o motivo, sem tocar no que já havia:
        sentimento e spam do resultado padrão não são opinião. Sucesso troca
        sentimento, spam e funcionalidades pelos da análise. Não faz commit.
        Devolve ``True`` se a IA analisou.
        """
        failure = result.get('failure')
        if failure:
            feedback.analysis_status = ANALYSIS_UNANALYZED
            feedback.analysis_error = failure
            return False

        feedback.sentiment = result.get('sentiment', 'INCONCLUSIVO')
        feedback.is_spam = result.get('is_spam', False)
        feedback.analysis_status = ANALYSIS_ANALYZED
        feedback.analysis_error = None
        feedback.analyzed_by = result.get('analyzed_by')
        feedback.requested_features.clear()
        for feature in result.get('requested_features', []):
            feedback.requested_features.append(RequestedFeature(
                # A coluna é VARCHAR(50): no Postgres um código maior derruba o
                # INSERT inteiro, então o limite é garantido aqui, na fronteira.
                code=str(feature['code'])[:FEATURE_CODE_MAX_LENGTH],
                reason=feature['reason'],
            ))
        return True

    @staticmethod
    def reanalyze(feedback: Feedback, analyzer) -> Optional[str]:
        """Roda a IA de novo no texto e grava o resultado (cadeia do servidor).

        Devolve ``None`` se a IA analisou, ou o motivo da falha
        (``rate_limit``, ``timeout``...), caso em que o feedback segue como
        não analisado. Levanta ``LLMNotConfiguredError`` sem provedor.
        """
        result = analyzer.analyze_feedback(feedback.feedback_text)
        analyzed = FeedbackService.apply_analysis(feedback, result)
        db.session.commit()
        return None if analyzed else result['failure']

    @staticmethod
    def apply_review(
        feedback: Feedback,
        is_spam: Optional[bool] = None,
        sentiment: Optional[str] = None,
    ) -> bool:
        """Correção humana da análise da IA: spam e/ou sentimento.

        Só mexe no que foi informado (``None`` = não alterar); as funcionalidades
        continuam as da análise. Estatísticas, listas e o resumo semanal passam a
        refletir a correção na próxima leitura, porque todos leem esses campos.
        Devolve ``True`` se algo mudou (repetir o mesmo valor não grava).
        """
        changed = False
        if is_spam is not None and feedback.is_spam != is_spam:
            feedback.is_spam = is_spam
            changed = True
        if sentiment is not None and feedback.sentiment != sentiment:
            feedback.sentiment = sentiment
            changed = True
        if changed:
            db.session.commit()
        return changed

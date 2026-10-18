from typing import List, Optional, Dict
from app import db
from app.models.feedback import Feedback
from sqlalchemy import desc, false, true
from sqlalchemy.orm import selectinload

# O que a listagem faz com o que a IA marcou como spam.
SPAM_EXCLUDE = 'exclude'
SPAM_INCLUDE = 'include'
SPAM_ONLY = 'only'
SPAM_MODES = (SPAM_EXCLUDE, SPAM_INCLUDE, SPAM_ONLY)

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
    ) -> Dict:
        """Get paginated feedbacks with optional filters

        ``spam`` decide o que a IA marcou como ruído: ``exclude`` (padrão, só o
        que não é spam), ``include`` (tudo) ou ``only`` (só os spams, para
        revisão humana).
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

        pagination = query.order_by(
            desc(Feedback.created_at)
        ).paginate(page=page, per_page=per_page)

        return {
            'items': pagination.items,
            'pagination': pagination,
            'sentiment': sentiment
        }

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

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
    def set_spam(feedback: Feedback, is_spam: bool) -> Feedback:
        """Corrige a classificação de spam feita pela IA (revisão humana).

        Só mexe em ``is_spam``: sentimento e funcionalidades continuam os da
        análise. Estatísticas, listas e o resumo semanal passam a refletir a
        correção na próxima leitura, porque todos filtram por esse campo.
        """
        feedback.is_spam = is_spam
        db.session.commit()
        return feedback

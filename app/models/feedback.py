from datetime import datetime, timezone
from typing import Dict, List
from sqlalchemy import String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship, Mapped, mapped_column
import uuid

from app import db
from app.models.types import GUID

# Tamanho da coluna requested_features.code (VARCHAR). Fonte única para o
# modelo e para quem grava, já que o código vem da saída da LLM.
FEATURE_CODE_MAX_LENGTH = 50

# Estado da análise da IA. "unanalyzed" = a IA não respondeu (cota, timeout,
# resposta inválida...): sentimento e spam são só o valor padrão, não opinião.
ANALYSIS_ANALYZED = 'analyzed'
ANALYSIS_UNANALYZED = 'unanalyzed'
ANALYSIS_STATUSES = (ANALYSIS_ANALYZED, ANALYSIS_UNANALYZED)


def as_utc(value: datetime) -> datetime:
    """Devolve ``value`` com fuso UTC explícito.

    O Postgres devolve ``timestamptz`` já com fuso; o SQLite devolve datetimes
    ingênuos (gravados em UTC). Normalizar mantém o JSON da API igual nos dois.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Feedback(db.Model):
    """
    Model for storing user feedback

    Attributes:
        id: Unique identifier (UUID)
        feedback_text: The actual feedback text
        sentiment: Analyzed sentiment (POSITIVO, NEGATIVO, INCONCLUSIVO)
        is_spam: Whether the feedback was classified as spam
        analysis_status: 'analyzed' ou 'unanalyzed' (a IA não respondeu)
        analysis_error: motivo da última falha da IA (rate_limit, timeout...)
        analyzed_by: 'provedor/modelo' que analisou (None se não analisado)
        created_at: When the feedback was created
        requested_features: List of features requested in this feedback
    """
    __tablename__ = 'feedbacks'

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4)
    feedback_text: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    analysis_status: Mapped[str] = mapped_column(
        String(12), nullable=False, default=ANALYSIS_ANALYZED,
        server_default=ANALYSIS_ANALYZED, index=True)
    analysis_error: Mapped[str | None] = mapped_column(String(20), nullable=True)
    analyzed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        index=True)

    # Relationship with requested features
    requested_features: Mapped[List['RequestedFeature']] = relationship(
        'RequestedFeature',
        back_populates='feedback',
        cascade='all, delete-orphan'
    )

    def __repr__(self) -> str:
        """String representation of the feedback"""
        return f'<Feedback {self.id}>'

    def to_dict(self) -> Dict:
        """
        Convert the model to a dictionary

        Returns:
            Dict containing the feedback data and its requested features
        """
        return {
            'id': str(self.id),
            'feedback': self.feedback_text,
            'sentiment': self.sentiment,
            'is_spam': self.is_spam,
            'analysis_status': self.analysis_status,
            'analysis_error': self.analysis_error,
            'analyzed_by': self.analyzed_by,
            'created_at': as_utc(self.created_at).isoformat(),
            'requested_features': [feature.to_dict() for feature in self.requested_features]
        }


class RequestedFeature(db.Model):
    """
    Model for storing features requested in feedbacks

    Attributes:
        id: Unique identifier (UUID)
        feedback_id: ID of the feedback this feature was requested in
        code: Feature code/identifier
        reason: Why this feature was identified in the feedback
        created_at: When the feature request was created
        feedback: Reference to the parent feedback
    """
    __tablename__ = 'requested_features'

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4)
    feedback_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey('feedbacks.id'), nullable=False, index=True)
    code: Mapped[str] = mapped_column(
        String(FEATURE_CODE_MAX_LENGTH), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationship with feedback
    feedback: Mapped['Feedback'] = relationship(
        'Feedback', back_populates='requested_features')

    def __repr__(self) -> str:
        """String representation of the requested feature"""
        return f'<RequestedFeature {self.code}>'

    def to_dict(self) -> Dict:
        """
        Convert the model to a dictionary

        Returns:
            Dict containing the feature code and reason
        """
        return {
            'code': self.code,
            'reason': self.reason
        }

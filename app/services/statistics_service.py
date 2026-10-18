from typing import Dict, List, Optional, TypedDict
from sqlalchemy import func
from app import db
from app.models.feedback import ANALYSIS_ANALYZED, Feedback, RequestedFeature


class FeatureStats(TypedDict):
    code: str
    count: int
    percentage: float


class SentimentStats(TypedDict):
    POSITIVO: int
    NEGATIVO: int
    INCONCLUSIVO: int


class Statistics:
    """Service class to handle feedback statistics calculations

    ``analyzed_only``: deixa de fora os feedbacks que a IA não conseguiu
    analisar (``analysis_status = 'unanalyzed'``). O sentimento deles é só o
    valor padrão, não uma opinião, então não devem entrar em números e
    gráficos. O painel usa ``analyzed_only=True``; o padrão (False) preserva o
    comportamento de quem chama sem o argumento.
    """

    @staticmethod
    def get_total_feedbacks(exclude_spam: bool = True, analyzed_only: bool = False) -> int:
        """Get total number of feedbacks, optionally excluding spam"""
        query = Feedback.query
        if exclude_spam:
            query = query.filter_by(is_spam=False)
        if analyzed_only:
            query = query.filter(Feedback.analysis_status == ANALYSIS_ANALYZED)
        return query.count()

    @staticmethod
    def _count_by_sentiment(
        exclude_spam: bool = True, analyzed_only: bool = False
    ) -> Dict[Optional[str], int]:
        """One GROUP BY with every sentiment bucket, including NULL."""
        query = db.session.query(
            Feedback.sentiment,
            func.count(Feedback.id).label('count')
        )

        if exclude_spam:
            query = query.filter(Feedback.is_spam == False)
        if analyzed_only:
            query = query.filter(Feedback.analysis_status == ANALYSIS_ANALYZED)

        return dict(query.group_by(Feedback.sentiment).all())

    @staticmethod
    def get_sentiment_counts(
        exclude_spam: bool = True, analyzed_only: bool = False
    ) -> SentimentStats:
        """Get counts of feedbacks by sentiment"""
        counts = Statistics._count_by_sentiment(exclude_spam, analyzed_only)
        return {
            'POSITIVO': counts.get('POSITIVO', 0),
            'NEGATIVO': counts.get('NEGATIVO', 0),
            'INCONCLUSIVO': counts.get('INCONCLUSIVO', 0)
        }

    @staticmethod
    def get_sentiment_percentages(total_feedbacks: int, sentiment_counts: SentimentStats) -> Dict[str, float]:
        """Calculate percentages for each sentiment"""
        if total_feedbacks == 0:
            return {
                'POSITIVO': 0.0,
                'NEGATIVO': 0.0,
                'INCONCLUSIVO': 0.0
            }

        return {
            sentiment: (count / total_feedbacks * 100)
            for sentiment, count in sentiment_counts.items()
        }

    @staticmethod
    def get_top_features(
        limit: int = 10,
        exclude_spam: bool = True,
        total_feedbacks: Optional[int] = None,
        analyzed_only: bool = False,
    ) -> List[FeatureStats]:
        """Get most requested features

        Args:
            total_feedbacks: denominator for the percentages. Pass it when the
                caller already counted, to skip a second COUNT query.
        """
        if total_feedbacks is None:
            total_feedbacks = Statistics.get_total_feedbacks(exclude_spam, analyzed_only)

        query = db.session.query(
            RequestedFeature.code,
            func.count(RequestedFeature.code).label('count')
        ).join(Feedback)

        if exclude_spam:
            query = query.filter(Feedback.is_spam == False)
        if analyzed_only:
            query = query.filter(Feedback.analysis_status == ANALYSIS_ANALYZED)

        features = query.group_by(
            RequestedFeature.code
        ).order_by(
            func.count(RequestedFeature.code).desc()
        ).limit(limit).all()

        return [
            {
                'code': feature.code,
                'count': feature.count,
                'percentage': (feature.count / total_feedbacks * 100) if total_feedbacks > 0 else 0
            }
            for feature in features
        ]

    @classmethod
    def get_dashboard_statistics(cls) -> Dict:
        """Get all statistics needed for the dashboard

        Two queries: one GROUP BY by sentiment (whose sum is the total, the
        same rows COUNT(*) would see) and the feature ranking. Fora spam e fora
        os feedbacks que a IA não analisou (sentimento só padrão): eles
        continuam na listagem e no aviso de pendentes, mas não nos números.
        """
        # Sempre excluir spam
        by_sentiment = cls._count_by_sentiment(exclude_spam=True, analyzed_only=True)
        total_feedbacks = sum(by_sentiment.values())

        if total_feedbacks == 0:
            return {'has_data': False}

        sentiment_counts: SentimentStats = {
            'POSITIVO': by_sentiment.get('POSITIVO', 0),
            'NEGATIVO': by_sentiment.get('NEGATIVO', 0),
            'INCONCLUSIVO': by_sentiment.get('INCONCLUSIVO', 0)
        }

        return {
            'has_data': True,
            'total_feedbacks': total_feedbacks,
            'sentiment_counts': sentiment_counts,
            'sentiment_percentages': cls.get_sentiment_percentages(total_feedbacks, sentiment_counts),
            'top_features': cls.get_top_features(
                exclude_spam=True, total_feedbacks=total_feedbacks,
                analyzed_only=True)
        }

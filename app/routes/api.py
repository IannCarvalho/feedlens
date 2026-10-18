import uuid
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, current_app
from typing import Dict, Any
from sqlalchemy.exc import IntegrityError
from app import db
from app.auth import api_read_required, auth_enabled, is_authenticated
from app.csrf import CSRF_HEADER, csrf_is_valid
from app.extensions import limiter
from app.ingest_auth import ingest_key_required
from app.models.feedback import Feedback
from app.services.feedback_analyzer import FeedbackAnalyzer
from app.services.feedback_service import (
    ANALYSIS_ANY,
    ANALYSIS_FILTERS,
    SENTIMENTS,
    SPAM_EXCLUDE,
    SPAM_MODES,
    FeedbackService,
)
from app.services.statistics_service import Statistics
from app.llm import (
    ByokHeaderError,
    LLMNotConfiguredError,
    LLMRequestError,
    credential_from_headers,
)

api = Blueprint('api', __name__)
feedback_analyzer = FeedbackAnalyzer()
logger = logging.getLogger(__name__)


def validate_feedback_request() -> Dict[str, Any]:
    """
    Validate feedback request data

    Returns:
        Dict containing the validated data

    Raises:
        ValueError if validation fails
    """
    if not request.is_json:
        raise ValueError('Request must be valid JSON')

    # silent=True: JSON inválido vira None e cai no 400 JSON abaixo, em vez
    # do 400 em HTML do Werkzeug. null/lista/número também não são objetos.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Request body must be a JSON object')

    if 'id' not in data or 'feedback' not in data:
        raise ValueError('Fields "id" and "feedback" are required')

    # Validate that the id is a well-formed UUID. Doing it here returns a
    # clean 400 instead of letting a malformed id blow up at commit time.
    try:
        uuid.UUID(str(data['id']))
    except (ValueError, AttributeError, TypeError):
        raise ValueError('Field "id" must be a valid UUID') from None

    feedback = data['feedback']
    if not isinstance(feedback, str):
        raise ValueError('Field "feedback" must be a string')

    if not feedback.strip():
        raise ValueError('Field "feedback" must not be empty')

    # Teto de tamanho: o texto vai inteiro para a LLM (custo por token).
    max_chars = current_app.config.get('MAX_FEEDBACK_CHARS', 5000)
    if len(feedback) > max_chars:
        raise ValueError(
            f'Field "feedback" must have at most {max_chars} characters')

    return data


@api.route('/feedbacks', methods=['POST'])
@limiter.limit(lambda: current_app.config.get('RATELIMIT_INGEST', '30 per minute'))
@ingest_key_required
def analyze_feedback():
    """
    Endpoint to receive and analyze feedback

    Receives a JSON with feedback ID and text, analyzes sentiment,
    spam status and identifies requested features.
    """
    try:
        data = validate_feedback_request()
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    feedback_id = data['id']
    feedback_text = data['feedback']

    # BYOK por requisição (opcional): X-LLM-Provider + X-LLM-Api-Key.
    try:
        credential = credential_from_headers(request.headers)
    except ByokHeaderError as e:
        return jsonify({'error': str(e), 'code': 'invalid_byok_headers'}), e.status_code

    # Check if feedback already exists
    if FeedbackService.get_feedback_by_id(feedback_id):
        return jsonify({'error': 'Feedback with this ID already exists'}), 409

    # Analyze feedback
    logger.info('Analyzing feedback id=%s', feedback_id)
    try:
        analysis_result = feedback_analyzer.analyze_feedback(
            feedback_text, credential=credential)
    except LLMNotConfiguredError:
        return jsonify({
            'error': 'Nenhum provedor de LLM configurado: defina uma chave no '
                     'servidor ou envie X-LLM-Provider e X-LLM-Api-Key.',
            'code': 'llm_not_configured',
        }), 503
    except LLMRequestError as e:
        # Só acontece com BYOK: a chave/modelo do próprio cliente falhou, então
        # nada é gravado e ele pode reenviar o mesmo id.
        if e.auth_failed:
            return jsonify({
                'error': 'O provedor recusou a chave enviada em X-LLM-Api-Key.',
                'code': 'llm_auth_failed',
            }), 422
        return jsonify({
            'error': 'O provedor de LLM falhou com a credencial enviada.',
            'code': 'llm_request_failed',
            'detail': e.attempts[-1].kind if e.attempts else None,
        }), 502
    sentiment = analysis_result.get('sentiment', 'INCONCLUSIVO')
    is_spam = analysis_result.get('is_spam', False)
    requested_features = analysis_result.get('requested_features', [])
    failure = analysis_result.get('failure')
    if failure:
        # A IA não respondeu (cota, timeout...): grava o feedback assim mesmo,
        # marcado como não analisado, para achar e reprocessar depois.
        logger.warning(
            'Feedback id=%s saved WITHOUT analysis (%s); reanalyze later',
            feedback_id, failure)
    else:
        logger.info(
            'Feedback id=%s analyzed: sentiment=%s is_spam=%s features=%d',
            feedback_id, sentiment, is_spam, len(requested_features))

    # Create feedback in database
    feedback = Feedback(
        id=feedback_id,
        feedback_text=feedback_text,
        sentiment=sentiment,
        is_spam=is_spam
    )
    FeedbackService.apply_analysis(feedback, analysis_result)

    # Save to database
    db.session.add(feedback)
    try:
        db.session.commit()
    except IntegrityError:
        # Duas requisições com o mesmo id passam juntas pela checagem acima;
        # a PK decide no commit, e o perdedor recebe o mesmo 409.
        db.session.rollback()
        return jsonify({'error': 'Feedback with this ID already exists'}), 409

    # Return result
    return jsonify({
        'id': str(feedback.id),
        'sentiment': feedback.sentiment,
        'is_spam': feedback.is_spam,
        'requested_features': [feature.to_dict() for feature in feedback.requested_features],
        # 'provedor/modelo' que fez a análise; null = a IA não analisou.
        'analyzed_by': analysis_result.get('analyzed_by'),
        # 'unanalyzed' quando a IA falhou; sentiment/is_spam são só o padrão.
        'analysis_status': feedback.analysis_status,
        'analysis_error': feedback.analysis_error,
    }), 201


@api.route('/feedbacks', methods=['GET'])
@api_read_required
def list_feedbacks():
    """
    List analyzed feedbacks, newest first. Spam is hidden unless asked for.

    Query params:
        page: page number (default 1)
        per_page: items per page (default 20, capped at 100)
        sentiment: optional filter (POSITIVO/NEGATIVO/INCONCLUSIVO)
        spam: exclude (default, no spam) | include (everything) | only (spam
            only, to review what the AI flagged)
        analysis: any (default) | unanalyzed (only feedbacks the AI could not
            analyze: quota, timeout...)
    """
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
    except (TypeError, ValueError):
        return jsonify({'error': 'page and per_page must be integers'}), 400

    if page < 1 or per_page < 1:
        return jsonify({'error': 'page and per_page must be positive'}), 400

    spam = request.args.get('spam', SPAM_EXCLUDE)
    if spam not in SPAM_MODES:
        return jsonify({
            'error': f'spam must be one of: {", ".join(SPAM_MODES)}',
        }), 400

    analysis = request.args.get('analysis', ANALYSIS_ANY)
    if analysis not in ANALYSIS_FILTERS:
        return jsonify({
            'error': f'analysis must be one of: {", ".join(ANALYSIS_FILTERS)}',
        }), 400

    # Cap per_page to keep a single response bounded.
    per_page = min(per_page, 100)

    sentiment = request.args.get('sentiment')
    result = FeedbackService.get_filtered_feedbacks(
        sentiment=sentiment, page=page, per_page=per_page, spam=spam,
        analysis=analysis)
    pagination = result['pagination']

    return jsonify({
        'items': [item.to_dict() for item in result['items']],
        'pagination': {
            'page': pagination.page,
            'per_page': pagination.per_page,
            'total': pagination.total,
            'pages': pagination.pages,
            'has_next': pagination.has_next,
            'has_prev': pagination.has_prev,
        },
        'sentiment': sentiment,
        'spam': spam,
        'analysis': analysis,
    }), 200


def panel_write_required(view):
    """Sessão do painel + CSRF para alterar um feedback, com respostas JSON.

    Diferente da leitura, não aceita ``API_READ_TOKEN``: corrigir a análise é
    uma ação de quem opera o painel. Sem ``DASHBOARD_PASSWORD`` o painel é
    aberto, e deixar qualquer um reescrever classificações não é aceitável.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not auth_enabled():
            return jsonify({
                'error': 'Defina DASHBOARD_PASSWORD para corrigir a '
                         'classificação de um feedback.',
                'code': 'auth_not_configured',
            }), 403
        if not is_authenticated():
            return jsonify({
                'error': 'Faça login no painel.',
                'code': 'unauthenticated',
            }), 401
        if not csrf_is_valid():
            return jsonify({
                'error': f'Header {CSRF_HEADER} ausente ou inválido.',
                'code': 'csrf',
            }), 403
        return view(*args, **kwargs)
    return wrapped


@api.route('/feedbacks/<feedback_id>', methods=['PATCH'])
@panel_write_required
def review_feedback(feedback_id):
    """
    Revisão humana da análise da IA: ``{"is_spam": bool}`` e/ou
    ``{"sentiment": "POSITIVO"|"NEGATIVO"|"INCONCLUSIVO"}``.

    Só esses dois campos podem mudar (as funcionalidades continuam as da
    análise; outras chaves do corpo são ignoradas). Pelo menos um é obrigatório.
    Idempotente: repetir o mesmo valor devolve 200 sem alterar nada.
    """
    try:
        uuid.UUID(str(feedback_id))
    except (ValueError, AttributeError, TypeError):
        return jsonify({'error': 'Field "id" must be a valid UUID'}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Body must be a JSON object'}), 400

    is_spam = data.get('is_spam')
    sentiment = data.get('sentiment')
    if is_spam is None and sentiment is None:
        return jsonify({
            'error': 'Send "is_spam" (boolean) and/or "sentiment" '
                     f'({", ".join(SENTIMENTS)})',
        }), 400
    if is_spam is not None and not isinstance(is_spam, bool):
        return jsonify({'error': 'Field "is_spam" must be a boolean'}), 400
    if sentiment is not None and sentiment not in SENTIMENTS:
        return jsonify({
            'error': f'Field "sentiment" must be one of: {", ".join(SENTIMENTS)}',
        }), 400

    feedback = FeedbackService.get_feedback_by_id(feedback_id)
    if feedback is None:
        return jsonify({'error': 'Feedback not found'}), 404

    if FeedbackService.apply_review(feedback, is_spam=is_spam, sentiment=sentiment):
        logger.info('Feedback id=%s revisado: is_spam=%s sentiment=%s',
                    feedback_id, feedback.is_spam, feedback.sentiment)

    return jsonify(feedback.to_dict()), 200


@api.route('/feedbacks/<feedback_id>/reanalyze', methods=['POST'])
@panel_write_required
def reanalyze_feedback(feedback_id):
    """
    Roda a IA de novo num feedback (cadeia de provedores do servidor).

    Pensado para os "não analisados" (cota, timeout...), mas vale para
    qualquer feedback: um resultado novo substitui sentimento, spam e
    funcionalidades, inclusive correções manuais anteriores. Síncrono: a
    chamada espera a IA (até ``LLM_TIMEOUT`` por tentativa).

    200 com o feedback atualizado; 503 se a IA não respondeu de novo
    (``analysis_unavailable``, com o motivo em ``detail``) ou se não há
    provedor configurado (``llm_not_configured``).
    """
    try:
        uuid.UUID(str(feedback_id))
    except (ValueError, AttributeError, TypeError):
        return jsonify({'error': 'Field "id" must be a valid UUID'}), 400

    feedback = FeedbackService.get_feedback_by_id(feedback_id)
    if feedback is None:
        return jsonify({'error': 'Feedback not found'}), 404

    try:
        failure = FeedbackService.reanalyze(feedback, feedback_analyzer)
    except LLMNotConfiguredError:
        return jsonify({
            'error': 'Nenhum provedor de LLM configurado: defina uma chave no '
                     'servidor ou cadastre uma em Provedores.',
            'code': 'llm_not_configured',
        }), 503

    if failure:
        logger.warning('Feedback id=%s continua sem análise (%s)', feedback_id, failure)
        return jsonify({
            'error': 'A IA não respondeu. O feedback continua como não analisado.',
            'code': 'analysis_unavailable',
            'detail': failure,
            'feedback': feedback.to_dict(),
        }), 503

    logger.info('Feedback id=%s reanalisado: sentiment=%s is_spam=%s',
                feedback_id, feedback.sentiment, feedback.is_spam)
    return jsonify(feedback.to_dict()), 200


@api.route('/feedbacks/<feedback_id>', methods=['GET'])
@api_read_required
def get_feedback(feedback_id):
    """
    Fetch a single feedback by its UUID.

    Returns 400 for a malformed id and 404 when it does not exist.
    """
    try:
        uuid.UUID(str(feedback_id))
    except (ValueError, AttributeError, TypeError):
        return jsonify({'error': 'Field "id" must be a valid UUID'}), 400

    feedback = FeedbackService.get_feedback_by_id(feedback_id)
    if feedback is None:
        return jsonify({'error': 'Feedback not found'}), 404

    return jsonify(feedback.to_dict()), 200


@api.route('/stats', methods=['GET'])
@api_read_required
def dashboard_stats():
    """Estatísticas do dashboard + feedbacks recentes, para o painel Vue.

    Espelha o que ``get_dashboard_statistics`` já calcula (duas queries) e
    anexa os recentes. Mesma proteção dos GET de feedbacks (sessão do painel
    ou ``API_READ_TOKEN``), já que expõe o conteúdo dos feedbacks.
    """
    stats = Statistics.get_dashboard_statistics()
    unanalyzed = FeedbackService.count_unanalyzed()
    if not stats.get('has_data'):
        return jsonify({'has_data': False, 'unanalyzed': unanalyzed}), 200

    recent = FeedbackService.get_recent_feedbacks()
    return jsonify({
        'has_data': True,
        'total_feedbacks': stats['total_feedbacks'],
        'sentiment_counts': stats['sentiment_counts'],
        'sentiment_percentages': stats['sentiment_percentages'],
        'top_features': stats['top_features'],
        'recent_feedbacks': [item.to_dict() for item in recent],
        # Feedbacks que a IA não conseguiu analisar (entram nos números acima
        # como INCONCLUSIVO, que aí é só o valor padrão).
        'unanalyzed': unanalyzed,
    }), 200

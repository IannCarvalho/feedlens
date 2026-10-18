"""Estado da análise da IA em cada feedback.

Antes, quando a IA falhava (cota, timeout...), o feedback era gravado com o
resultado seguro padrão (INCONCLUSIVO, sem spam) e nada o distinguia de uma
opinião neutra de verdade. Agora há um status, o motivo da falha e o
provedor/modelo que analisou, para achar e reprocessar os não analisados.

Linhas que já existem ficam como 'analyzed': não há como saber, depois do fato,
quais foram resultado padrão. Use ``flask reanalyze-pending --id <uuid>`` ou o
botão "Reanalisar" do painel nos que você suspeitar.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN simples: o SQLite aceita (com DEFAULT) sem recriar a tabela.
    op.add_column('feedbacks', sa.Column(
        'analysis_status', sa.String(length=12), nullable=False,
        server_default='analyzed'))
    op.add_column('feedbacks', sa.Column(
        'analysis_error', sa.String(length=20), nullable=True))
    op.add_column('feedbacks', sa.Column(
        'analyzed_by', sa.String(length=100), nullable=True))
    op.create_index('ix_feedbacks_analysis_status', 'feedbacks', ['analysis_status'])


def downgrade():
    # batch: o SQLite só apaga coluna recriando a tabela; no Postgres vira ALTER.
    with op.batch_alter_table('feedbacks') as batch:
        batch.drop_index('ix_feedbacks_analysis_status')
        batch.drop_column('analyzed_by')
        batch.drop_column('analysis_error')
        batch.drop_column('analysis_status')

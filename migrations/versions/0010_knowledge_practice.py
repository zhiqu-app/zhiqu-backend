"""记录稳定知识点关联及题目来源；保留历史名称和作答快照。"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('questions', 'attempts'):
        op.add_column(table, sa.Column('knowledge_node_id', sa.String(64), nullable=True))
    op.add_column('questions', sa.Column('source_evidence', sa.JSON(), nullable=True))

    nodes = sa.table('knowledge_nodes', sa.column('user_id'), sa.column('course_id'),
                     sa.column('id'), sa.column('label'))
    for name in ('questions', 'attempts'):
        rows = sa.table(name, sa.column('user_id'), sa.column('course_id'),
                        sa.column('point'), sa.column('knowledge_node_id'))
        # 只有同账号、同课程内唯一同名的历史记录自动关联。
        node_id = sa.select(sa.func.min(nodes.c.id)).where(
            nodes.c.user_id == rows.c.user_id, nodes.c.course_id == rows.c.course_id,
            nodes.c.label == rows.c.point).having(sa.func.count() == 1).scalar_subquery()
        op.execute(rows.update().values(knowledge_node_id=node_id))

    op.create_index('ix_question_course_node', 'questions', ['user_id', 'course_id', 'knowledge_node_id'])
    op.create_index('ix_attempt_course_node', 'attempts', ['user_id', 'course_id', 'knowledge_node_id'])


def downgrade():
    op.drop_index('ix_attempt_course_node', table_name='attempts')
    op.drop_index('ix_question_course_node', table_name='questions')
    op.drop_column('questions', 'source_evidence')
    for table in ('attempts', 'questions'):
        op.drop_column(table, 'knowledge_node_id')

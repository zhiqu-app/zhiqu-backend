"""仅在随机隔离 MySQL 测试库中验证迁移和跨课程外键。"""
from pathlib import Path
import os
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, event
from sqlalchemy.exc import IntegrityError
from app.models import Base, KnowledgeNode
from test_review_mysql import assert_fixture_schema
from test_group_migration import seed_graph, assert_graph_preserved
from test_knowledge_groups import grouped, course_graph

pytestmark=pytest.mark.skipif(os.getenv('RUN_MYSQL_TESTS')!='1',reason='Requires random fixture-owned MySQL schema')


def test_mysql_0007_preserves_graph(database):
    root=Path(__file__).resolve().parents[1]
    config=Config(str(root/'alembic.ini'))
    with database.engine.begin() as connection:
        assert_fixture_schema(connection)
        Base.metadata.drop_all(connection)
        config.attributes['connection']=connection
        command.upgrade(config,'0006')
        seed_graph(connection)
        command.upgrade(config,'0007')
        assert_graph_preserved(connection)


def test_mysql_fk_rejects_foreign_course_and_reset_cascades(client,auth,database):
    endpoint=course_graph(client,auth)
    assert client.put(endpoint,headers=auth,json=grouped()).status_code==200
    second=client.post('/api/v1/courses',headers=auth,json={'name':'别的课程'}).json()['id']
    with database.sessions() as db:
        node=db.scalar(select(KnowledgeNode).where(KnowledgeNode.course_id==endpoint.split('/')[-2]))
        owner=node.user_id
    with pytest.raises(IntegrityError), database.sessions.begin() as db:
        db.add(KnowledgeNode(user_id=owner,course_id=second,id='foreign',label='不允许',group_id='c0'))
    errors=[]
    def capture_error(context): errors.append(str(context.original_exception))
    event.listen(database.engine, 'handle_error', capture_error)
    response=client.post('/api/v1/data/reset',headers=auth,json={'confirm':'RESET_MY_LEARNING_DATA'})
    event.remove(database.engine, 'handle_error', capture_error)
    assert response.status_code==200, errors
    assert client.get('/api/v1/courses',headers=auth).json()['total']==0

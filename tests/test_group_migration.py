"""大知识点迁移只增加可空归属，原节点/连线/任务关联保持不变。"""
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select, inspect
from test_review_migration import seed_legacy_review_tasks


def seed_graph(connection):
    seed_legacy_review_tasks(connection)
    tables = {name: Table(name, MetaData(), autoload_with=connection) for name in ['knowledge_nodes','knowledge_edges','knowledge_task_links']}
    for i in range(2):
        connection.execute(tables['knowledge_nodes'].insert().values(user_id='owner',course_id='course',id=f'n{i}',label=f'点{i}',description='保留说明',x=100+i*200,y=100,sample=False))
    connection.execute(tables['knowledge_edges'].insert().values(user_id='owner',course_id='course',source='n0',target='n1'))
    connection.execute(tables['knowledge_task_links'].insert().values(user_id='owner',course_id='course',node_id='n0',task_id='custom_done'))


def assert_graph_preserved(connection):
    nodes = Table('knowledge_nodes',MetaData(),autoload_with=connection)
    edges = Table('knowledge_edges',MetaData(),autoload_with=connection)
    tasks = Table('knowledge_task_links',MetaData(),autoload_with=connection)
    values = connection.execute(select(nodes).order_by(nodes.c.id)).all()
    assert [(n.id,n.description,n.x,n.group_id) for n in values] == [('n0','保留说明',100,None),('n1','保留说明',300,None)]
    assert [(e.source,e.target) for e in connection.execute(select(edges))] == [('n0','n1')]
    assert connection.execute(select(tasks)).one().task_id == 'custom_done'
    assert 'knowledge_groups' in inspect(connection).get_table_names()


def test_sqlite_0006_to_0007_keeps_graph_and_task_bindings(tmp_path):
    root=Path(__file__).resolve().parents[1]
    engine=create_engine('sqlite:///'+(tmp_path/'groups-migrate.db').as_posix())
    config=Config(str(root/'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection']=connection
        command.upgrade(config,'0006')
        seed_graph(connection)
        command.upgrade(config,'0007')
        assert_graph_preserved(connection)
    engine.dispose()

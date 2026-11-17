"""验证同节点题库、来源快照及真正的变式复测。"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, Table, create_engine, select

from app.models import Base, Course, User
from test_review_mysql import assert_fixture_schema


def test_wrong_answer_to_cited_variant_and_renamed_node(client, auth):
    base = '/api/v1'
    created = client.post(base + '/courses', headers=auth, json={'name': '线代复测'})
    assert created.status_code == 201, created.text
    course = created.json()['id']
    endpoint = base + f'/courses/{course}/knowledge'
    graph = {'title': '线代复测', 'revision': 0, 'nodes': [
        {'id': 'vector', 'label': '特征向量', 'x': 100, 'y': 80},
        {'id': 'other', 'label': '矩阵乘法', 'x': 200, 'y': 80}], 'links': []}
    saved = client.put(endpoint, headers=auth, json=graph)
    assert saved.status_code == 200, saved.text
    assert saved.json()['nodes'][0]['reviewStatus'] == 'unpracticed'

    prefix, excerpt = '📝 复习讲义\n', '特征向量经过线性变换后方向不变。'
    material = client.post(base + f'/courses/{course}/materials', headers=auth,
        files={'file': ('讲义.txt', (prefix + excerpt).encode(), 'text/plain')})
    assert material.status_code == 201, material.text
    mid = material.json()['id']
    start = len(prefix.encode('utf-16-le')) // 2
    evidence = {'materialId': mid, 'name': '客户端名称', 'excerpt': excerpt,
                'start': start, 'end': start + len(excerpt)}

    def add(title, node='vector', **fields):
        return client.post(base + f'/courses/{course}/questions', headers=auth, json={
            'title': title, 'options': ['成立', '不成立'], 'answer': 0, 'explanation': excerpt,
            'point': '旧标签', 'knowledgeNodeId': node, **fields})

    original = add('若 Av=2v，v 是特征向量吗？', source=mid, sourceEvidence=evidence)
    assert original.status_code == 201, original.text
    assert original.json()['sourceEvidence'] is None
    assert original.json()['point'] == '特征向量'
    variant = add('若 Av=-v，v 是特征向量吗？')
    assert variant.status_code == 201, variant.text
    assert add('不相干的矩阵题', 'other').status_code == 201
    assert add('伪造引用', source=mid, sourceEvidence={**evidence, 'start': start - 1}).status_code == 422
    assert add('外部节点', 'eigen').status_code == 404
    listed = client.get(base + '/questions', headers=auth, params={'courseId': course, 'knowledgeNodeId': 'vector'}).json()
    assert {item['id'] for item in listed['items']} == {original.json()['id'], variant.json()['id']}
    oid, vid = original.json()['id'], variant.json()['id']

    def submit(qid, choice, sequence, task=None):
        response = client.post(base + '/attempts', headers=auth, json={
            'questionId': qid, 'choice': choice, 'submissionId': 'retest-record-' + sequence,
            **({'taskId': task} if task else {})})
        assert response.status_code == 200, response.text
        return response.json()

    wrong = submit(oid, 1, 'first')
    assert wrong['knowledgeNodeId'] == 'vector'
    assert wrong['sourceEvidence']['excerpt'] == excerpt
    assert wrong['sourceEvidence']['name'] == material.json()['name']
    mistakes = client.get(base + '/mistakes', headers=auth,
        params={'courseId': course, 'knowledgeNodeId': 'vector'}).json()
    assert mistakes['items'][0]['sourceEvidence'] == wrong['sourceEvidence']
    graph['revision'] = saved.json()['revision']
    graph['nodes'][0]['label'] = '特征向量与不变方向'
    renamed = client.put(endpoint, headers=auth, json=graph).json()
    assert renamed['nodes'][0]['reviewStatus'] == 'needs_review'
    assert renamed['nodes'][0]['practiceCount'] == 1
    point = client.get(base + '/reports/summary', headers=auth, params={'courseId': course}).json()['points'][0]
    assert point['name'] == '特征向量与不变方向'
    assert point['knowledgeNodeId'] == 'vector'

    plan = client.post(base + f'/mistakes/{oid}/retest', headers=auth).json()
    assert plan['created'] is True
    task = plan['task']
    assert task['questionIds'] == [vid]
    due = datetime.fromisoformat(task['reminderAt'])
    assert timedelta(days=1, hours=23) < due - datetime.now(timezone.utc) <= timedelta(days=2)
    assert client.post(base + f'/mistakes/{oid}/retest', headers=auth).json()['task']['id'] == task['id']
    submit(oid, 0, 'original-again')
    assert client.get(endpoint, headers=auth).json()['nodes'][0]['reviewStatus'] == 'needs_review'
    assert submit(vid, 0, 'variant', task['id'])['task']['done'] is True
    node = client.get(endpoint, headers=auth).json()['nodes'][0]
    assert node['reviewStatus'] == 'retest_passed'
    assert node['practiceCount'] == 3
    assert client.delete(base + '/tasks/' + task['id'], headers=auth).status_code == 204
    assert client.get(endpoint, headers=auth).json()['nodes'][0]['reviewStatus'] == 'retest_passed'
    assert client.post(base + f'/mistakes/{oid}/retest', headers=auth).status_code == 409


def test_link_existing_question_backfills_unlinked_attempts(client, auth):
    base = '/api/v1'
    created = client.post(base + '/courses/linear/questions', headers=auth, json={
        'title': '旧题等待整理', 'options': ['甲', '乙'], 'answer': 0, 'point': '早期标签', 'explanation': '解析'})
    assert created.status_code == 201, created.text
    question = created.json()
    assert question['knowledgeNodeId'] is None
    answer = {'questionId': question['id'], 'choice': 1, 'submissionId': 'legacy-link-001'}
    assert client.post(base + '/attempts', headers=auth, json=answer).json()['knowledgeNodeId'] is None
    endpoint = base + f'/courses/linear/questions/{question["id"]}/knowledge'
    assert client.patch(endpoint, headers=auth, json={'knowledgeNodeId': 'missing'}).status_code == 404
    assert client.patch(endpoint, headers=auth, json={'knowledgeNodeId': 'eigen'}).json()['knowledgeNodeId'] == 'eigen'
    historical = client.post(base + '/attempts', headers=auth, json=answer).json()
    assert historical['point'] == '早期标签'
    assert historical['knowledgeNodeId'] == 'eigen'


def assert_knowledge_link_upgrade(connection):
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.attributes['connection'] = connection
    command.upgrade(config, '0009')
    connection.execute(User.__table__.insert().values(id='owner', email='legacy-review@example.test'))
    connection.execute(Course.__table__.insert().values(user_id='owner', id='math', name='线代'))
    nodes = Table('knowledge_nodes', MetaData(), autoload_with=connection)
    questions = Table('questions', MetaData(), autoload_with=connection)
    attempts = Table('attempts', MetaData(), autoload_with=connection)
    for key, label in [('vector', '特征向量'), ('same1', '同名'), ('same2', '同名')]:
        connection.execute(nodes.insert().values(user_id='owner', course_id='math', id=key,
            label=label, description='', x=0, y=0, sample=False))
    for key, point in [('q1', '特征向量'), ('q2', '同名'), ('q3', '旧标签')]:
        connection.execute(questions.insert().values(user_id='owner', course_id='math', id=key,
            title='题目', options=['甲', '乙'], answer=0, explanation='解析', point=point,
            sample=False, created_at=datetime.now()))
        connection.execute(attempts.insert().values(user_id='owner', course_id='math', id=key,
            question_id=key, submission_id=key, choice=0, correct=True, seconds=0,
            point=point, question_snapshot={'point': point}, created_at=datetime.now()))
    command.upgrade(config, '0010')
    expected = [('特征向量', 'vector'), ('同名', None), ('旧标签', None)]
    for name in ('questions', 'attempts'):
        table = Table(name, MetaData(), autoload_with=connection)
        assert connection.execute(select(table.c.point, table.c.knowledge_node_id).order_by(table.c.id)).all() == expected


def test_knowledge_link_migration_preserves_legacy_names(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'knowledge-links.db').as_posix())
    with engine.begin() as connection:
        assert_knowledge_link_upgrade(connection)
    engine.dispose()


@pytest.mark.skipif(os.getenv('RUN_MYSQL_TESTS') != '1', reason='Requires an isolated MySQL test database')
def test_mysql_knowledge_link_migration(database):
    with database.engine.begin() as connection:
        assert_fixture_schema(connection)
        Base.metadata.drop_all(connection)
        assert_knowledge_link_upgrade(connection)

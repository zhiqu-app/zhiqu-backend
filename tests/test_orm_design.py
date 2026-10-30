from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.main import create_app
from app.config import Settings
from app.models import Base, Course, Material, Question, Attempt, Task

BASE='/api/v1'


def test_fresh_registration_has_no_fake_data(client,verified_register):
    response=verified_register({'email':'clean@example.com','password':'pass1234'})
    assert response.status_code==201
    auth={'Authorization':'Bearer '+response.json()['accessToken']}
    for resource in ['/courses','/materials','/tasks','/attempts','/mistakes']:
        assert client.get(BASE+resource,headers=auth).json()['total']==0
    assert client.get(BASE+'/reports/summary',headers=auth).json()['total']==0


def test_course_progress_is_derived(client,auth):
    assert client.get(BASE+'/courses/linear',headers=auth).json()['progress']==0
    response=client.patch(BASE+'/courses/linear',headers=auth,json={'completedLessons':9})
    assert response.status_code==200 and response.json()['progress']==50
    assert client.patch(BASE+'/courses/linear',headers=auth,json={'lessons':8}).status_code==422
    assert client.patch(BASE+'/courses/linear',headers=auth,json={'progress':99}).status_code==422


def test_manual_real_question_and_empty_course(client,auth):
    course=client.post(BASE+'/courses',headers=auth,json={'name':'网络基础'}).json()
    assert client.get(BASE+'/questions?courseId='+course['id'],headers=auth).json()['total']==0
    body={'title':'TCP 属于哪一层？','options':['应用层','传输层'],'answer':1,'explanation':'TCP 是传输层协议。','point':'TCP'}
    result=client.post(BASE+f'/courses/{course["id"]}/questions',headers=auth,json=body)
    assert result.status_code==201 and 'answer' not in result.json()
    question=result.json()
    grade=client.post(BASE+'/attempts',headers=auth,json={'questionId':question['id'],'choice':1,'seconds':5,'submissionId':'network_0001'})
    assert grade.status_code==200 and grade.json()['attempt']['correct'] is True
    assert client.get(BASE+'/reports/summary?courseId='+course['id'],headers=auth).json()['points'][0]['name']=='TCP'
    assert client.post(BASE+f'/courses/{course["id"]}/questions',headers=auth,json={**body,'source':'sample-linear'}).status_code==422
    assert client.post(BASE+f'/courses/{course["id"]}/questions',headers=auth,json={**body,'answer':3}).status_code==422


def test_graph_database_nodes_and_edges(client,auth):
    course=client.post(BASE+'/courses',headers=auth,json={'name':'图谱课程'}).json()['id']
    node_path=BASE+f'/courses/{course}/knowledge/nodes'
    first=client.post(node_path,headers=auth,json={'label':'定义','description':'实际维护的内容','x':100,'y':100}).json()
    second=client.post(node_path,headers=auth,json={'label':'应用','x':300,'y':150}).json()
    edge=BASE+f'/courses/{course}/knowledge/edges'
    assert client.post(edge,headers=auth,json={'source':first['id'],'target':second['id']}).status_code==201
    assert client.post(edge,headers=auth,json={'source':first['id'],'target':second['id']}).status_code==409
    assert client.post(edge,headers=auth,json={'source':first['id'],'target':'eigen'}).status_code==404
    graph=client.get(BASE+'/knowledge?courseId='+course,headers=auth).json()
    assert len(graph['nodes'])==2 and graph['edges']==[[0,1]] and graph['sample'] is False


def test_attempt_snapshot_survives_question_change(client,auth,database):
    body={'questionId':'q1','choice':0,'seconds':5,'submissionId':'snapshot_0001'}
    first=client.post(BASE+'/attempts',headers=auth,json=body).json()
    with database.sessions.begin() as db:
        question=db.scalars(select(Question).where(Question.id=='q1')).one()
        question.answer=0
        question.explanation='管理员之后修改了题库'
    repeated=client.post(BASE+'/attempts',headers=auth,json=body).json()
    assert repeated==first


def test_pending_plan_unique_key_and_reopen(client,auth):
    client.post(BASE+'/attempts',headers=auth,json={'questionId':'q1','choice':0,'submissionId':'wrong_0001'})
    first=client.post(BASE+'/mistakes/q1/plan',headers=auth).json()['task']
    assert client.patch(BASE+'/tasks/'+first['id'],headers=auth,json={'done':True}).status_code==422
    assert client.post(BASE+'/attempts',headers=auth,json={'questionId':'q1','choice':0,
        'submissionId':'plan_complete_0001','taskId':first['id']}).status_code==200
    second=client.post(BASE+'/mistakes/q1/plan',headers=auth).json()['task']
    assert first['id']!=second['id']
    assert client.patch(BASE+'/tasks/'+first['id'],headers=auth,json={'done':False}).status_code==422
    assert client.get(BASE+'/tasks?status=done',headers=auth).json()['total']==1


def test_reset_default_clears_instead_of_reinventing_samples(client,auth):
    assert client.post(BASE+'/data/reset',headers=auth,json={'confirm':'RESET_MY_LEARNING_DATA'}).status_code==200
    assert client.get(BASE+'/courses',headers=auth).json()['total']==0
    assert client.get(BASE+'/auth/me',headers=auth).status_code==200


def test_blob_and_text_are_deferred(database,client,auth):
    from sqlalchemy import event
    assert inspect(Material).attrs.content.deferred
    assert inspect(Material).attrs.text_content.deferred
    statements=[]
    def record(connection,cursor,statement,parameters,context,executemany):
        statements.append(statement)
    event.listen(database.engine,'before_cursor_execute',record)
    try:
        assert client.get(BASE+'/materials',headers=auth).status_code==200
    finally:
        event.remove(database.engine,'before_cursor_execute',record)
    material_queries=[sql for sql in statements if 'FROM materials' in sql]
    assert material_queries
    assert all('materials.content' not in sql and 'materials.text_content' not in sql for sql in material_queries)


def test_mysql_ddl_has_binary_storage_precision_and_foreign_keys():
    material=str(CreateTable(Material.__table__).compile(dialect=mysql.dialect()))
    attempt=str(CreateTable(Attempt.__table__).compile(dialect=mysql.dialect()))
    assert 'LONGBLOB' in material and 'LONGTEXT' in material
    assert 'DATETIME(6)' in attempt and 'uq_attempt_submission' in attempt
    assert 'FOREIGN KEY(user_id, question_id)' in attempt
    assert 'ENGINE=InnoDB' in material and 'utf8mb4' in material
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=mysql.dialect()))


def test_frozen_migration_roundtrip(tmp_path):
    root=Path(__file__).resolve().parents[1]
    engine=create_engine('sqlite:///'+(tmp_path/'migration.db').as_posix())
    config=Config(str(root/'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection']=connection
        command.upgrade(config,'head')
        assert compare_metadata(MigrationContext.configure(connection),Base.metadata)==[]
        command.downgrade(config,'base')
        assert set(inspect(connection).get_table_names())=={'alembic_version'}
    engine.dispose()


def test_runtime_url_is_mysql_and_password_hidden():
    settings=Settings(mysql_password='has@#:/characters',ai_base_url='',ai_api_key='',ai_model='')
    assert settings.database_url.drivername=='mysql+pymysql'
    assert settings.database_url.password=='has@#:/characters'
    assert settings.mysql_password not in repr(settings)
    assert settings.mysql_password not in str(settings.database_url)


def test_openapi_contract_without_db():
    schema=create_app().openapi()
    assert BASE+'/courses/{course_id}/questions' in schema['paths']
    assert BASE+'/courses/{course_id}/knowledge/nodes' in schema['paths']


def test_source_offsets_match_javascript_utf16():
    from app.services import retrieve
    text='📚说明\n  特征值是主对角线元素。  '
    source=retrieve('特征值', [{'id':'m','name':'讲义','text':text}])[0]
    encoded=text.encode('utf-16-le')
    assert encoded[source['start']*2:source['end']*2].decode('utf-16-le')==source['excerpt']

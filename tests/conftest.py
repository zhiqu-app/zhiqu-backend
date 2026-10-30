"""默认 SQLite 隔离业务回归；RUN_MYSQL_TESTS=1 使用真正的 MySQL 随机测试库。"""
from pathlib import Path
import os
import re
import secrets
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
(Path(__file__).resolve().parents[1] / 'test-results').mkdir(exist_ok=True)
from app.config import Settings
from app.database import Database
from app.models import Base
from app.main import create_app


@pytest.fixture
def database(tmp_path):
    if os.getenv('RUN_MYSQL_TESTS') == '1':
        if not os.getenv('TEST_MYSQL_USER'):
            pytest.fail('请配置 TEST_MYSQL_USER 与 TEST_MYSQL_PASSWORD，测试账号需能创建/删除隔离测试库')
        name = 'zhiqu_orm_test_' + secrets.token_hex(12)
        settings = Settings(mysql_host=os.getenv('TEST_MYSQL_HOST','127.0.0.1'),
            mysql_port=int(os.getenv('TEST_MYSQL_PORT','3306')), mysql_database=name,
            mysql_user=os.environ['TEST_MYSQL_USER'], mysql_password=os.getenv('TEST_MYSQL_PASSWORD',''),
            ai_base_url='', ai_model='', ai_api_key='')
        admin = create_engine(settings.database_url._replace(database=None), poolclass=NullPool, isolation_level='AUTOCOMMIT', hide_parameters=True)
        created = False
        try:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
            created = True
            db = Database(settings)
            Base.metadata.create_all(db.engine)
            yield db
        finally:
            if created:
                db.close()
                if re.fullmatch(r'zhiqu_orm_test_[0-9a-f]{24}',name):
                    with admin.connect() as connection:
                        connection.exec_driver_sql(f'DROP DATABASE `{name}`')
            admin.dispose()
    else:
        engine = create_engine('sqlite:///' + (tmp_path/'business.db').as_posix(),
            connect_args={'check_same_thread':False})
        @event.listens_for(engine, 'connect')
        def pragmas(connection, _):
            connection.isolation_level = None
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
        @event.listens_for(engine, 'begin')
        def begin(connection):
            connection.exec_driver_sql('BEGIN')
        db = Database(engine=engine)
        Base.metadata.create_all(engine)
        yield db
        db.close()


@pytest.fixture
def settings(database):
    value = Settings(ai_base_url='', ai_model='', ai_api_key='')
    value._testing_database = database
    return value


@pytest.fixture
def client(settings, database):
    with TestClient(create_app(settings, database=database)) as client:
        yield client


@pytest.fixture
def auth(client):
    response = client.post('/api/v1/auth/demo')
    assert response.status_code == 201, response.text
    return {'Authorization':'Bearer ' + response.json()['accessToken']}


@pytest.fixture
def verified_register(client, settings):
    """让原有账号回归显式完成验证码流程；邮件仅捕获在测试进程内。"""
    settings.smtp_host='smtp.example.test'
    settings.smtp_username=settings.smtp_from='sender@qq.com'
    settings.smtp_password='test-authorization'
    settings.verification_secret='test-only-registration-secret-with-32-characters'
    outbox={}
    def capture(config,email,code):
        outbox[email]=code
    client.app.state.mailer=capture
    def register(body):
        email=body['email'].strip().lower()
        if email not in outbox:
            response=client.post('/api/v1/auth/email-code',json={'email':email})
            assert response.status_code==200,response.text
        return client.post('/api/v1/auth/register',json={**body,'verificationCode':outbox[email]})
    return register

"""真实 HTTP 传输回归；默认仍用隔离测试引擎，不能冒充真实 MySQL 联测。"""
import socket
import threading
import time

import httpx
import uvicorn

from app.main import create_app


def test_real_http_commit_before_next_request(settings,database):
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(settings,database=database),
        host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+8
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.02)
        assert server.started,'HTTP 测试服务器启动失败'
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=5,trust_env=False) as client:
            assert client.get('/api/v1/health').json()['status']=='ok'
            assert client.get('/docs').status_code==200
            response=client.post('/api/v1/auth/demo')
            assert response.status_code==201
            client.headers['Authorization']='Bearer '+response.json()['accessToken']
            # 这里不能 sleep；捕获“响应发出后才提交”的认证/持久化竞态。
            assert client.get('/api/v1/auth/me').status_code==200
            course=client.post('/api/v1/courses',json={'name':'真实HTTP课程'}).json()
            assert client.get('/api/v1/courses/'+course['id']).json()['name']=='真实HTTP课程'
            upload=client.post(f'/api/v1/courses/{course["id"]}/materials',
                files={'file':('notes.txt','真实HTTP资料'.encode(),'text/plain')})
            assert upload.status_code==201
            assert client.get('/api/v1/materials/'+upload.json()['id']+'/file').content=='真实HTTP资料'.encode()
            attempt=client.post('/api/v1/attempts',json={'questionId':'q1','choice':0,'seconds':12,'submissionId':'real_http_0001'})
            assert attempt.status_code==200
            assert client.get('/api/v1/reports/summary').json()['total']==1
            client.post('/api/v1/auth/logout')
            assert client.get('/api/v1/auth/me').status_code==401
    finally:
        server.should_exit=True
        thread.join(timeout=8)
        sock.close()
    assert not thread.is_alive()

"""PDF 跨会话、便捷图谱编辑版本与现有接口行为回归。"""
from io import BytesIO
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from app.main import create_app


def registered(client, verified_register):
    response=verified_register({'email':'persist@example.test','password':'Safe-password-123','name':'测试同学'})
    assert response.status_code==201
    return {'Authorization':'Bearer '+response.json()['accessToken']}


def test_pdf_survives_fresh_app_and_login(client, verified_register, database, settings):
    headers=registered(client,verified_register)
    course=client.post('/api/v1/courses',headers=headers,json={'name':'PDF课程'}).json()
    writer=PdfWriter();writer.add_blank_page(width=300,height=400);stream=BytesIO();writer.write(stream)
    binary=stream.getvalue()
    response=client.post(f'/api/v1/courses/{course["id"]}/materials',headers=headers,files={'file':('lesson.pdf',binary,'application/pdf')})
    assert response.status_code==201,response.text
    material=response.json()
    with TestClient(create_app(settings,database=database)) as fresh:
        result=fresh.post('/api/v1/auth/login',json={'email':'persist@example.test','password':'Safe-password-123'})
        auth={'Authorization':'Bearer '+result.json()['accessToken']}
        listed=fresh.get('/api/v1/materials',headers=auth,params={'courseId':course['id']}).json()['items']
        assert listed[0]['name']=='lesson.pdf'
        assert fresh.get('/api/v1/materials/'+material['id']+'/file',headers=auth).content==binary


def test_quick_graph_mutations_invalidate_whole_graph_revision(client,auth):
    course=client.post('/api/v1/courses',headers=auth,json={'name':'快速维护'}).json()
    endpoint=f'/api/v1/courses/{course["id"]}/knowledge'
    payload={'title':'自定义图','goal':'','revision':0,'nodes':[],'links':[]}
    assert client.put(endpoint,headers=auth,json=payload).status_code==200
    node=client.post(endpoint+'/nodes',headers=auth,json={'label':'新节点','x':100,'y':100})
    assert node.status_code==201
    graph=client.get(endpoint,headers=auth).json()
    assert graph['revision']==2
    assert client.put(endpoint,headers=auth,json={**payload,'revision':1}).status_code==409


def test_quick_connection_cannot_introduce_cycle(client,auth):
    course=client.post('/api/v1/courses',headers=auth,json={'name':'顺序关系'}).json()
    endpoint=f'/api/v1/courses/{course["id"]}/knowledge'
    a=client.post(endpoint+'/nodes',headers=auth,json={'label':'先学'}).json()['id']
    b=client.post(endpoint+'/nodes',headers=auth,json={'label':'后学'}).json()['id']
    assert client.post(endpoint+'/edges',headers=auth,json={'source':a,'target':b}).status_code==201
    assert client.post(endpoint+'/edges',headers=auth,json={'source':b,'target':a}).status_code==422


def test_deleting_course_removes_only_its_generated_mistake_tasks(client,auth):
    before=client.get('/api/v1/tasks',headers=auth).json()['total']
    course=client.post('/api/v1/courses',headers=auth,json={'name':'要删除的课程'}).json()
    question=client.post(f'/api/v1/courses/{course["id"]}/questions',headers=auth,json={
        'title':'测试题','options':['正确','错误'],'answer':0,'explanation':'解析','point':'知识点'}).json()
    assert client.post('/api/v1/attempts',headers=auth,json={'questionId':question['id'],'choice':1,'seconds':1,'submissionId':'delete-task-001'}).status_code==200
    assert client.post('/api/v1/mistakes/'+question['id']+'/plan',headers=auth).status_code==200
    assert client.delete('/api/v1/courses/'+course['id'],headers=auth).status_code==204
    assert client.get('/api/v1/tasks',headers=auth).json()['total']==before

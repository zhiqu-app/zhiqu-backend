"""新建与编辑最多六点；超额保存不能影响旧图。"""
from app.models import KnowledgeNode


def test_six_nodes_save_seventh_rejected_with_coordinates_preserved(client,auth):
    course=client.post('/api/v1/courses',headers=auth,json={'name':'稳定位置'}).json()['id']
    endpoint=f'/api/v1/courses/{course}/knowledge'
    payload={'title':'我的图','revision':0,'groups':[{'id':'g','title':'可靠传输'}],
             'nodes':[{'id':f'n{i}','label':f'概念{i}','x':100+i*60,'y':80+i*20,'groupId':'g'} for i in range(6)],'links':[]}
    response=client.put(endpoint,headers=auth,json=payload)
    assert response.status_code==200,response.text
    original=response.json()
    assert {(n['id'],n['x'],n['y']) for n in original['nodes']}=={(n['id'],n['x'],n['y']) for n in payload['nodes']}
    payload['revision']=1
    payload['nodes'].append({'id':'extra','label':'第七个','x':600,'y':150,'groupId':'g'})
    assert client.put(endpoint,headers=auth,json=payload).status_code==422
    assert client.get(endpoint,headers=auth).json()==original


def test_legacy_groups_of_six_keep_original_coordinates(client,auth,database):
    course=client.post('/api/v1/courses',headers=auth,json={'name':'旧知识'}).json()['id']
    user=client.get('/api/v1/auth/me',headers=auth).json()['id']
    with database.sessions.begin() as db:
        db.add_all([KnowledgeNode(user_id=user,course_id=course,id=f'n{i:02}',label=f'旧{i}',x=100+i*30,y=100) for i in range(13)])
    graph=client.get(f'/api/v1/courses/{course}/knowledge',headers=auth).json()
    assert [sum(n['groupId']==g['id'] for n in graph['nodes']) for g in graph['groups']]==[6,6,1]
    assert all(n['x']==100+int(n['id'][1:])*30 and n['y']==100 for n in graph['nodes'])

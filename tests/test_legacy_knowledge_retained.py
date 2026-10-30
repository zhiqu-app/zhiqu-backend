"""上一版保存的课程/数学或学科节点保留为各自课程历史图，不再创建账号模板。"""
from app.models import KnowledgeConfig


def test_legacy_course_nodes_remain_readable(client, auth, database):
    user = client.get('/api/v1/auth/me', headers=auth).json()
    # 体验账号已有 linear 课程与节点；模拟上一版只保存过账号关联的情况。
    with database.sessions.begin() as db:
        db.add(KnowledgeConfig(user_id=user['id'], course_id='linear', subject='network', level='beginner', goal='历史配置'))
    response = client.get('/api/v1/courses/linear/knowledge', headers=auth)
    assert response.status_code == 200
    assert response.json()['generationMode'] == 'legacy'
    assert response.json()['nodes']
    assert response.json()['course']['id'] == 'linear'

"""仅通过显式 seedDemo 或体验入口导入样例；正式注册默认没有业务数据。"""
import json
from pathlib import Path

from .models import Course, Material, Question, Task, TaskSubtask, KnowledgeNode, KnowledgeEdge

SEED = json.loads((Path(__file__).parent / 'data/seed.json').read_text(encoding='utf-8'))


def seed_user(db, user_id):
    """为指定账号导入显式样例课程、资料、题库、任务和图谱，不伪造作答或完成进度。"""
    for value in SEED['courses']:
        db.add(Course(user_id=user_id, **{key: value[key] for key in [
            'id', 'name', 'teacher', 'description', 'chapter', 'lessons', 'cover', 'archived']},
            completed_lessons=0, sample=True))
    db.flush()
    for value in SEED['materials']:
        content = value['text'].encode('utf-8')
        db.add(Material(user_id=user_id, id=value['id'], course_id=value['courseId'], name=value['name'],
            kind='TXT', content=content, text_content=value['text'], size_bytes=len(content),
            media_type='text/plain; charset=utf-8', sample=True, parse_status='ready'))
    nodes = [('matrix', '矩阵', 70, 160), ('operation', '矩阵运算', 170, 72),
             ('linear', '线性方程组', 260, 190), ('eigen', '特征值', 385, 105),
             ('vector', '特征向量', 490, 213), ('diagonal', '对角化', 603, 141),
             ('application', '应用', 688, 65)]
    node_ids = {label: key for key, label, _, _ in nodes}
    for value in SEED['questions']:
        db.add(Question(user_id=user_id, id=value['id'], course_id='linear',
            title=value['title'], options=value['options'], answer=value['answer'],
            explanation=value['explanation'], point=value['point'], knowledge_node_id=node_ids.get(value['point']),
            source_id=value['source'], sample=True))
    for value in SEED['tasks']:
        db.add(Task(user_id=user_id, id=value['id'], title=value['title'], description=value['description'],
            minutes=value['minutes'], target=value['target'],
            subtasks=[TaskSubtask(user_id=user_id, task_id=value['id'], title=value['title'], position=0, done=False)]))
    for key, label, x, y in nodes:
        db.add(KnowledgeNode(user_id=user_id, course_id='linear', id=key, label=label,
            description='示例知识点：' + label, x=x, y=y, sample=True))
    db.flush()
    for source, target in [(0,1),(0,2),(1,2),(1,3),(2,3),(3,4),(3,5),(4,5),(5,6)]:
        db.add(KnowledgeEdge(user_id=user_id, course_id='linear', source=nodes[source][0], target=nodes[target][0]))
    db.flush()

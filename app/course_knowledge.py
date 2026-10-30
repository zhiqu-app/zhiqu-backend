"""按已存在的课程手动维护知识星图；整图事务保存，可选关联本人复习任务。"""
from typing import Annotated, Literal
from collections import Counter

from fastapi import APIRouter, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import delete, select

from .dependencies import CurrentUser, DB, own
from .models import Course, CourseKnowledgeConfig, KnowledgeGroup, KnowledgeNode, KnowledgeEdge, KnowledgeTaskLink, Task
from .knowledge_groups import group_rows, group_unassigned, validate_membership
from .parameters import CourseId
from .responses import KnowledgeOut, KnowledgeNodeOut, CourseOut, ERROR_RESPONSES
from .schemas import Input, NodeCreate, EdgeCreate
from .serializers import course_view
from .study import knowledge


class ManualNode(NodeCreate):
    id: Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')] = Field(
        description='节点稳定 ID，字母数字下划线连字符；新增时前端生成，编辑时保留。')
    taskId: Annotated[str, Field(min_length=1, max_length=64)] | None = Field(
        default=None, description='可选的本人复习任务 ID；不关联时传 null。')
    groupId: Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')] | None = Field(
        default=None, description='所属大知识点 ID。提供 groups 时必填，每组最多 6 个小知识点。')


class ManualGroup(Input):
    id: Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')] = Field(description='本课程内稳定大知识点 ID，编辑和改名时保留。')
    title: Annotated[str, Field(min_length=1, max_length=80)] = Field(description='大知识点名称，例如网络基础。')


class ManualGraph(Input):
    title: Annotated[str, Field(min_length=1, max_length=80)] = Field(description='用户填写的本课程星图标题。')
    goal: Annotated[str, Field(max_length=180)] = Field(default='', description='本次课程学习目标或任务概述，可留空。')
    revision: Annotated[int, Field(ge=0, strict=True)] = Field(description='读取接口返回的版本号；首次保存为 0，冲突返回 409。')
    nodes: Annotated[list[ManualNode], Field(max_length=50)] = Field(description='用户设置的全部知识点；空数组表示清空本课程星图。')
    links: Annotated[list[EdgeCreate], Field(max_length=150)] = Field(description='知识点先后关系，使用节点 ID；必须无环、无重复、无自连。')
    groups: Annotated[list[ManualGroup], Field(max_length=50)] | None = Field(
        default=None, description='按展示顺序提交本课程全部大知识点，每组最多 6 点。旧客户端不传时保留原分组并兼容未分组节点。')

    @model_validator(mode='after')
    def relationships_valid(self):
        """在写库前检查节点唯一性和有向无环关系，避免部分保存。"""
        ids = {node.id for node in self.nodes}
        if self.groups is not None:
            group_ids = {group.id for group in self.groups}
            if len(group_ids) != len(self.groups):
                raise ValueError('大知识点 ID 不能重复')
            if any(node.groupId not in group_ids for node in self.nodes):
                raise ValueError('每个知识点必须选择本次保存的大知识点')
            if any(count > 6 for count in Counter(node.groupId for node in self.nodes).values()):
                raise ValueError('一个大知识点最多包含 6 个知识点')
        if len(ids) != len(self.nodes):
            raise ValueError('知识点 ID 不能重复')
        pairs = set()
        children = {node_id: [] for node_id in ids}
        degree = {node_id: 0 for node_id in ids}
        for link in self.links:
            if link.source not in ids or link.target not in ids:
                raise ValueError('连线的起点和终点必须在本次知识点中')
            pair = (link.source, link.target)
            if pair in pairs:
                raise ValueError('不能重复连接同一对知识点')
            pairs.add(pair)
            children[link.source].append(link.target)
            degree[link.target] += 1
        ready = [key for key, value in degree.items() if value == 0]
        visited = 0
        while ready:
            visited += 1
            source = ready.pop()
            for target in children[source]:
                degree[target] -= 1
                if degree[target] == 0:
                    ready.append(target)
        if visited != len(ids):
            raise ValueError('知识点先后关系不能形成循环')
        return self


class CourseNodeOut(KnowledgeNodeOut):
    groupId: str = Field(description='所属大知识点 ID。历史未分组节点返回稳定的临时大知识点 ID。')
    taskId: str | None = Field(description='可选关联的本人复习任务 ID；任务被删除后为 null。')
    taskTitle: str | None = Field(description='关联任务当前的标题。')
    taskDone: bool | None = Field(description='关联任务当前是否完成；未关联时为 null，不等同于知识掌握评分。')


class GroupOut(ManualGroup):
    legacy: bool = Field(description='true 表示历史节点的临时分组，保存后变为正式大知识点。')


class CourseGraphOut(KnowledgeOut):
    groups: list[GroupOut] = Field(description='用户自定义的大知识点列表，顺序与保存一致；每个包含最多六个小知识点。')
    course: CourseOut = Field(description='已经存在的本人课程，星图保存不会新建或改名课程。')
    id: str = Field(description='课程星图 ID，使用所属课程 ID。')
    title: str = Field(description='星图标题。')
    goal: str = Field(description='用户设置的学习目标。')
    revision: int = Field(description='整图保存版本号，下次保存原样提交。')
    configured: bool = Field(description='本课程是否已保存配置或已有历史节点；不作为登录门槛。')
    generationMode: Literal['manual', 'legacy'] = Field(description='manual 手动配置，legacy 表示保留的历史节点。')
    nodes: list[CourseNodeOut] = Field(description='本课程知识点及可选复习任务，顺序与 edges 下标配套。')


router = APIRouter(tags=['课程知识星图'], responses={code: ERROR_RESPONSES[code] for code in (401, 404, 409, 422, 503)})


def graph_view(course, user, db):
    """读取本课程节点、配置和任务状态，不在读取时创建示例数据。"""
    graph = knowledge(user, db, course.id)
    config = db.get(CourseKnowledgeConfig, (user.id, course.id))
    bindings = db.execute(select(KnowledgeTaskLink.node_id, Task).join(Task,
        (Task.user_id == KnowledgeTaskLink.user_id) & (Task.id == KnowledgeTaskLink.task_id)).where(
        KnowledgeTaskLink.user_id == user.id, KnowledgeTaskLink.course_id == course.id)).all()
    tasks = {node_id: task for node_id, task in bindings}
    assignment = dict(db.execute(select(KnowledgeNode.id, KnowledgeNode.group_id).where(
        KnowledgeNode.user_id == user.id, KnowledgeNode.course_id == course.id)).all())
    groups = [{'id': c.id, 'title': c.title, 'legacy': False} for c in group_rows(db, user.id, course.id)]
    for node in graph['nodes']:
        node['groupId'] = assignment[node['id']]
        task = tasks.get(node['id'])
        node.update(taskId=task.id if task else None, taskTitle=task.title if task else None,
                    taskDone=task.done if task else None)
    group_unassigned(groups, graph['nodes'])
    return {**graph, 'groups': groups, 'id': course.id, 'course': course_view(course),
            'title': config.title if config else course.name + '知识星图',
            'goal': config.goal if config else '', 'revision': config.revision if config else 0,
            'configured': bool(config or graph['nodes']), 'generationMode': 'manual' if config else 'legacy'}


@router.get('/courses/{course_id}/knowledge', summary='查询课程知识星图', response_model=CourseGraphOut)
def course_graph(course_id: CourseId, user: CurrentUser, db: DB) -> dict:
    """只读取已存在且属于当前账号的课程；新课程返回空节点，允许稍后再配置。"""
    return graph_view(own(db, Course, user.id, course_id), user, db)


@router.put('/courses/{course_id}/knowledge', summary='保存课程的完整手动星图', response_model=CourseGraphOut)
def save_course_graph(course_id: CourseId, body: ManualGraph, user: CurrentUser, db: DB) -> dict:
    """在课程行锁与同一事务内保存知识点、先后关系和任务关联；版本冲突不覆盖旧图。"""
    course = db.scalar(select(Course).where(Course.user_id == user.id, Course.id == course_id).with_for_update())
    if course is None:
        raise HTTPException(404, '课程不存在或无权访问')
    config = db.get(CourseKnowledgeConfig, (user.id, course_id))
    revision = config.revision if config else 0
    if body.revision != revision:
        raise HTTPException(409, '星图已被更新，请重新加载后再编辑；当前填写内容尚未保存')
    for task_id in {node.taskId for node in body.nodes if node.taskId}:
        own(db, Task, user.id, task_id)
    old_nodes = db.scalars(select(KnowledgeNode).where(
        KnowledgeNode.user_id == user.id, KnowledgeNode.course_id == course_id)).all()
    previous_groups = group_rows(db, user.id, course_id)
    old_assignment = {row.id: row.group_id for row in old_nodes}
    nodes = [node.model_dump() for node in body.nodes]
    if body.groups is None:
        groups = [{'id': c.id, 'title': c.title} for c in previous_groups]
        for node in nodes:
            node['groupId'] = node['groupId'] or old_assignment.get(node['id'])
        group_unassigned(groups, nodes)
    else:
        groups = [group.model_dump() for group in body.groups]
    validate_membership(groups, nodes)
    for position, group in enumerate(groups):
        row = db.get(KnowledgeGroup, (user.id, course_id, group['id']))
        if row is None:
            row = KnowledgeGroup(user_id=user.id, course_id=course_id, id=group['id'])
            db.add(row)
        row.title, row.position = group['title'], position
    db.flush()
    # 全部校验完成后才开始替换关系；提交失败由请求事务统一回滚。
    db.execute(delete(KnowledgeEdge).where(KnowledgeEdge.user_id == user.id, KnowledgeEdge.course_id == course_id))
    db.execute(delete(KnowledgeTaskLink).where(KnowledgeTaskLink.user_id == user.id, KnowledgeTaskLink.course_id == course_id))
    incoming = {node.id for node in body.nodes}
    for row in old_nodes:
        if row.id not in incoming:
            db.delete(row)
    db.flush()
    for node in nodes:
        row = db.get(KnowledgeNode, (user.id, course_id, node['id']))
        if row is None:
            row = KnowledgeNode(user_id=user.id, course_id=course_id, id=node['id'])
            db.add(row)
        for key in ('label', 'description', 'x', 'y'):
            setattr(row, key, node[key])
        row.group_id = node['groupId']
        row.sample = False
    db.flush()
    kept_groups = {group['id'] for group in groups}
    for group in previous_groups:
        if group.id not in kept_groups:
            db.delete(group)
    db.flush()
    for link in body.links:
        db.add(KnowledgeEdge(user_id=user.id, course_id=course_id, source=link.source, target=link.target))
    for node in body.nodes:
        if node.taskId:
            db.add(KnowledgeTaskLink(user_id=user.id, course_id=course_id, node_id=node.id, task_id=node.taskId))
    if config is None:
        config = CourseKnowledgeConfig(user_id=user.id, course_id=course_id)
        db.add(config)
    config.title, config.goal, config.revision = body.title, body.goal, revision + 1
    db.flush()
    return graph_view(course, user, db)


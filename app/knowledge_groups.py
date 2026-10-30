"""大知识点归属与历史节点分组，不在查询接口中改写数据库。"""
from collections import Counter
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import select

from .models import KnowledgeGroup


def group_rows(db, user_id, course_id):
    """读取当前用户当前课程的大知识点，保持用户保存的顺序。"""
    return db.scalars(select(KnowledgeGroup).where(
        KnowledgeGroup.user_id == user_id, KnowledgeGroup.course_id == course_id
    ).order_by(KnowledgeGroup.position, KnowledgeGroup.id)).all()


def group_unassigned(groups, nodes):
    """为无归属历史节点每六点生成一个稳定临时组；已有大知识点和节点 ID 保持原样。"""
    unassigned = [node for node in nodes if not node.get('groupId')]
    ids = {group['id'] for group in groups}
    for offset in range(0, len(unassigned), 6):
        batch = unassigned[offset:offset + 6]
        seed = batch[0]['id']
        group_id = 'legacy-' + sha256(seed.encode()).hexdigest()[:24]
        while group_id in ids:
            seed += '-'
            group_id = 'legacy-' + sha256(seed.encode()).hexdigest()[:24]
        ids.add(group_id)
        groups.append({'id': group_id, 'title': f'待整理大知识点 {offset // 6 + 1}', 'legacy': True})
        for node in batch:
            node['groupId'] = group_id
    return groups


def validate_membership(groups, nodes):
    """验证每个知识点归属本次课程大知识点，每组最多六点；保存前调用保证事务完整。"""
    ids = {group['id'] for group in groups}
    if any(node.get('groupId') not in ids for node in nodes):
        raise HTTPException(422, '每个知识点必须选择本课程的大知识点')
    if any(count > 6 for count in Counter(node['groupId'] for node in nodes).values()):
        raise HTTPException(422, '一个大知识点最多包含 6 个知识点，请拆分大知识点或移动知识点')


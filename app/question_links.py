"""题目入库前验证课程节点、资料归属及可追溯的原文片段。"""
from fastapi import HTTPException
from sqlalchemy import select

from .dependencies import own
from .models import KnowledgeNode, Material
from .services import pdf_page_number


def require_node(db, user_id, course_id, node_id):
    """只返回指定账号、课程下的节点，避免跨课程关联。"""
    node = db.get(KnowledgeNode, (user_id, course_id, node_id))
    if node is None:
        raise HTTPException(404, '当前课程没有这个知识点')
    return node


def question_fields(db, user_id, course_id, body):
    """生成与 ORM 一致的字段，并将节点名称作为历史快照。"""
    values = body.model_dump(exclude={'source', 'knowledgeNodeId', 'sourceEvidence'})
    if body.knowledgeNodeId:
        node = require_node(db, user_id, course_id, body.knowledgeNodeId)
        values.update(point=node.label, knowledge_node_id=node.id)
    else:
        matches = db.execute(select(KnowledgeNode.id, KnowledgeNode.label).where(
            KnowledgeNode.user_id == user_id, KnowledgeNode.course_id == course_id,
            KnowledgeNode.label == body.point))
        ids = [node_id for node_id, label in matches if label == body.point]
        if len(ids) == 1:
            values['knowledge_node_id'] = ids[0]

    evidence = body.sourceEvidence
    source_id = body.source or (evidence.materialId if evidence else None)
    if source_id:
        material = own(db, Material, user_id, source_id)
        if material.course_id != course_id:
            raise HTTPException(422, '题目来源必须属于当前课程')
        if evidence:
            values['source_evidence'] = verified_evidence(material, evidence)
    values['source_id'] = source_id
    return values


def verified_evidence(material, evidence):
    """核对 UTF-16 偏移和原文，使用服务器记录的资料名及页码。"""
    if evidence.materialId != material.id or material.parse_status != 'ready':
        raise HTTPException(422, '题目引用与可读取的资料不一致')
    text = material.text_content.encode('utf-16-le')
    start, end = evidence.start * 2, evidence.end * 2
    if end <= start or end > len(text) or text[start:end] != evidence.excerpt.encode('utf-16-le'):
        raise HTTPException(422, '题目引用已无法在资料原文定位，请重新生成')
    page = pdf_page_number(text[:start].decode('utf-16-le')) if material.kind == 'PDF' else None
    return {**evidence.model_dump(), 'name': material.name, 'page': page}

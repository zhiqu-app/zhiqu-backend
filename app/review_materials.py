"""为课程问答、出题和复习推荐提供可检索的讲义。"""
from sqlalchemy import select

from .models import Material


def ready_materials(db, user_id, course_id, material_id=None):
    """读取课程范围内可检索的资料正文，按需限定一份资料。"""
    statement = select(Material.id, Material.name, Material.kind, Material.text_content).where(
        Material.user_id == user_id, Material.course_id == course_id, Material.parse_status == 'ready')
    if material_id is not None:
        statement = statement.where(Material.id == material_id)
    return [{'id': row.id, 'name': row.name, 'kind': row.kind, 'text': row.text_content}
            for row in db.execute(statement.order_by(Material.created_at, Material.id))]

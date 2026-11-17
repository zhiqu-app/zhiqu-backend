"""业务字段独立建模；JSON 仅用于选项、不可变作答快照与引用片段。"""
from datetime import date, datetime, timezone
import secrets

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, false)
from sqlalchemy.dialects.mysql import DATETIME, LONGBLOB, LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    """生成表示 UTC 的无时区 datetime，供 MySQL DATETIME(6) 一致存储。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id():
    """生成 32 位随机十六进制资源 ID，不使用可猜测的自增业务 ID。"""
    return secrets.token_hex(16)


# MySQL 默认 DATETIME 无小数秒，会把同一秒多次作答的顺序压平。
Timestamp = DateTime().with_variant(DATETIME(fsp=6), 'mysql')


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(30), default='同学')
    major: Mapped[str] = mapped_column(String(30), default='')
    grade: Mapped[str] = mapped_column(String(10), default='其他')
    contact_email: Mapped[str] = mapped_column(String(100), default='')
    avatar: Mapped[str | None] = mapped_column(Text)
    reduced_motion: Mapped[bool] = mapped_column(Boolean, default=False)
    reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    courses: Mapped[list['Course']] = relationship(back_populates='owner', passive_deletes=True)
    sessions: Mapped[list['AuthSession']] = relationship(back_populates='owner', passive_deletes=True)


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    expires_at: Mapped[datetime] = mapped_column(Timestamp, index=True)
    owner: Mapped[User] = relationship(back_populates='sessions')


class Course(Base):
    __tablename__ = 'courses'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(30))
    teacher: Mapped[str] = mapped_column(String(20), default='未设置')
    chapter: Mapped[str] = mapped_column(String(80), default='')
    description: Mapped[str] = mapped_column(String(180), default='')
    lessons: Mapped[int] = mapped_column(Integer, default=0)
    completed_lessons: Mapped[int] = mapped_column(Integer, default=0)
    cover: Mapped[str] = mapped_column(String(20), default='structures')
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    owner: Mapped[User] = relationship(back_populates='courses')
    materials: Mapped[list['Material']] = relationship(back_populates='course', passive_deletes=True)
    questions: Mapped[list['Question']] = relationship(back_populates='course', passive_deletes=True)
    __table_args__ = (
        CheckConstraint('lessons >= 0 AND completed_lessons >= 0 AND completed_lessons <= lessons', name='ck_course_lessons'),
        Index('ix_course_owner_created', 'user_id', 'created_at', 'id'),
    )


class KnowledgeConfig(Base):
    """每个账号当前选定的学习方向；课程被删除或重置时配置自动失效。"""
    __tablename__ = 'knowledge_configs'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(30))
    level: Mapped[str] = mapped_column(String(20))
    goal: Mapped[str] = mapped_column(String(180))
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    )


class Material(Base):
    __tablename__ = 'materials'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(5))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary().with_variant(LONGBLOB(), 'mysql'), deferred=True)
    text_content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), 'mysql'), default='', deferred=True)
    media_type: Mapped[str] = mapped_column(String(100))
    parse_status: Mapped[str] = mapped_column(String(20), default='ready')
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    course: Mapped[Course] = relationship(back_populates='materials')
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        Index('ix_material_course', 'user_id', 'course_id', 'created_at', 'id'),
    )


class Question(Base):
    __tablename__ = 'questions'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    options: Mapped[list] = mapped_column(JSON)
    answer: Mapped[int] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text)
    point: Mapped[str] = mapped_column(String(80))
    knowledge_node_id: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(64))
    source_evidence: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(Timestamp)
    course: Mapped[Course] = relationship(back_populates='questions')
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        CheckConstraint('answer >= 0 AND answer < 6', name='ck_question_answer'),
        Index('ix_question_course', 'user_id', 'course_id', 'created_at', 'id'),
        Index('ix_question_course_node', 'user_id', 'course_id', 'knowledge_node_id'),
    )


class Attempt(Base):
    __tablename__ = 'attempts'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32))
    course_id: Mapped[str] = mapped_column(String(64))
    question_id: Mapped[str] = mapped_column(String(64))
    submission_id: Mapped[str] = mapped_column(String(100))
    choice: Mapped[int] = mapped_column(Integer)
    correct: Mapped[bool] = mapped_column(Boolean)
    seconds: Mapped[int] = mapped_column(Integer)
    point: Mapped[str] = mapped_column(String(80))
    knowledge_node_id: Mapped[str | None] = mapped_column(String(64))
    question_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'question_id'], ['questions.user_id', 'questions.id'], ondelete='CASCADE'),
        UniqueConstraint('user_id', 'submission_id', name='uq_attempt_submission'),
        UniqueConstraint('user_id', 'id', name='uq_attempt_owner_id'),
        CheckConstraint('choice >= 0 AND choice < 6 AND seconds >= 0 AND seconds <= 86400', name='ck_attempt_values'),
        Index('ix_attempt_owner_date', 'user_id', 'created_at', 'id'),
        Index('ix_attempt_owner_course_date', 'user_id', 'course_id', 'created_at', 'id'),
        Index('ix_attempt_owner_question', 'user_id', 'question_id', 'created_at', 'id'),
        Index('ix_attempt_course_node', 'user_id', 'course_id', 'knowledge_node_id'),
    )


class Task(Base):
    __tablename__ = 'tasks'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(500), default='')
    minutes: Mapped[int] = mapped_column(Integer, default=15)
    planned_date: Mapped[date | None] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    target: Mapped[str] = mapped_column(String(200), default='/app/practice')
    from_question: Mapped[str | None] = mapped_column(String(64))
    active_key: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10), default='custom', server_default='custom')
    course_id: Mapped[str | None] = mapped_column(String(64))
    reminder_at: Mapped[datetime | None] = mapped_column(Timestamp)
    completed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    subtasks: Mapped[list['TaskSubtask']] = relationship(lazy='selectin', order_by='TaskSubtask.position', passive_deletes=True, cascade='all, delete-orphan')
    questions: Mapped[list['TaskQuestion']] = relationship(lazy='selectin', order_by='TaskQuestion.position', passive_deletes=True, cascade='all, delete-orphan')
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE', name='fk_task_course'),
        CheckConstraint("kind IN ('custom', 'exercise')", name='ck_task_kind'),
        Index('ix_task_due', 'done', 'reminder_at'),
        UniqueConstraint('user_id', 'active_key', name='uq_task_pending_question'),
        CheckConstraint('minutes >= 1 AND minutes <= 240', name='ck_task_minutes'),
        Index('ix_task_owner_date', 'user_id', 'planned_date', 'created_at', 'id'),
    )


class TaskSubtask(Base):
    __tablename__ = 'task_subtasks'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    done: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        CheckConstraint('position >= 0', name='ck_subtask_position'),
        Index('ix_subtask_owner_task_position', 'user_id', 'task_id', 'position'),
    )


class TaskQuestion(Base):
    __tablename__ = 'task_questions'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'question_id'], ['questions.user_id', 'questions.id'], ondelete='CASCADE'),
    )


class TaskAttempt(Base):
    __tablename__ = 'task_attempts'
    attempt_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'attempt_id'], ['attempts.user_id', 'attempts.id'], ondelete='CASCADE'),
    )


class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))
    occurrence_at: Mapped[datetime] = mapped_column(Timestamp)
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(String(500))
    target: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
        UniqueConstraint('user_id', 'task_id', 'kind', 'occurrence_at', name='uq_notification_task_occurrence'),
        CheckConstraint("kind IN ('due', 'completed')", name='ck_notification_kind'),
        Index('ix_notification_owner_created', 'user_id', 'created_at', 'id'),
    )


class KnowledgeGroup(Base):
    """用户在课程下自定义的大知识点；显示顺序与知识依赖关系分别存储。"""
    __tablename__ = 'knowledge_groups'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    )


class KnowledgeNode(Base):
    __tablename__ = 'knowledge_nodes'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default='')
    x: Mapped[int] = mapped_column(Integer, default=100)
    y: Mapped[int] = mapped_column(Integer, default=100)
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'course_id', 'group_id'],
            ['knowledge_groups.user_id', 'knowledge_groups.course_id', 'knowledge_groups.id'],
            name='fk_knowledge_node_group', ondelete='CASCADE'),
    )


class KnowledgeEdge(Base):
    __tablename__ = 'knowledge_edges'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    target: Mapped[str] = mapped_column(String(64), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id', 'source'], ['knowledge_nodes.user_id', 'knowledge_nodes.course_id', 'knowledge_nodes.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'course_id', 'target'], ['knowledge_nodes.user_id', 'knowledge_nodes.course_id', 'knowledge_nodes.id'], ondelete='CASCADE'),
        Index('ix_edge_target', 'user_id', 'course_id', 'target'),
    )


class CourseKnowledgeConfig(Base):
    """按用户和课程分别保存手动星图，不要求账号先配置全局学习方向。"""
    __tablename__ = 'course_knowledge_configs'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    goal: Mapped[str] = mapped_column(String(180), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
    )


class KnowledgeTaskLink(Base):
    """知识点可选关联一项本人任务；删除任务只解除关联，不删除知识点。"""
    __tablename__ = 'knowledge_task_links'
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id', 'node_id'], ['knowledge_nodes.user_id', 'knowledge_nodes.course_id', 'knowledge_nodes.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'task_id'], ['tasks.user_id', 'tasks.id'], ondelete='CASCADE'),
    )


class CoachHistory(Base):
    __tablename__ = 'review_coach_history'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32))
    course_id: Mapped[str] = mapped_column(String(64))
    course_name: Mapped[str] = mapped_column(String(30))
    goal: Mapped[str] = mapped_column(String(200))
    recommendation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'],
                             name='fk_coach_history_course', ondelete='CASCADE'),
        Index('ix_coach_history_owner_date', 'user_id', 'created_at', 'id'),
        Index('ix_coach_history_course_date', 'user_id', 'course_id', 'created_at', 'id'),
    )


class ChatTurn(Base):
    __tablename__ = 'chat_turns'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32))
    course_id: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(String(2000))
    text: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON)
    questions: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    mode: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'course_id'], ['courses.user_id', 'courses.id'], ondelete='CASCADE'),
        Index('ix_chat_course_date', 'user_id', 'course_id', 'created_at', 'id'),
    )


class EmailCodeSlot(Base):
    """验证码摘要、有效期、错误次数与发送配额。"""
    __abstract__ = True
    email: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(32))
    code_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    requested_at: Mapped[datetime] = mapped_column(Timestamp)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    window_started_at: Mapped[datetime] = mapped_column(Timestamp)
    window_count: Mapped[int] = mapped_column(Integer, default=0)


class EmailVerification(EmailCodeSlot):
    __tablename__ = 'email_verifications'
    __table_args__ = (
        CheckConstraint('failed_attempts >= 0 AND failed_attempts <= 5', name='ck_email_code_attempts'),
        CheckConstraint('window_count >= 0 AND window_count <= 5', name='ck_email_code_quota'),
    )


class PasswordResetVerification(EmailCodeSlot):
    __tablename__ = 'password_reset_verifications'
    __table_args__ = (
        CheckConstraint('failed_attempts >= 0 AND failed_attempts <= 5', name='ck_reset_code_attempts'),
        CheckConstraint('window_count >= 0 AND window_count <= 5', name='ck_reset_code_quota'),
    )


for table in Base.metadata.tables.values():
    table.dialect_options['mysql'].update(engine='InnoDB', charset='utf8mb4', collate='utf8mb4_bin')

"""具名响应模型：约束返回结构并在 Swagger 展示每个字段的含义。"""
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from .schemas import AuthOut, ProfileOut, QuestionCreate, QuestionEvidence, UserOut

T = TypeVar('T')


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(
        description='本页数据数组；没有数据时返回空数组。',
    )
    total: int = Field(
        description='匹配查询条件的记录总数，不只是本页数量。',
    )
    limit: int = Field(
        description='本次分页大小，最大 100。',
    )
    offset: int = Field(
        description='本次跳过的记录数，从 0 开始。',
    )


class HealthOut(BaseModel):
    status: str = Field(
        description='服务健康状态，正常为 ok。',
    )
    database: str = Field(
        description='正常运行时为 mysql；显式注入测试引擎时会标明测试引擎。',
    )
    version: str = Field(
        description='本接口服务版本。',
    )
    assistantMode: Literal['retrieval', 'llm'] = Field(
        description='当前问答模式：资料检索或已配置外部模型。',
    )


class CourseOut(BaseModel):
    id: str = Field(
        description='课程 ID，后续课程、资料和图谱查询使用此值。',
    )
    name: str = Field(
        description='课程名称，可用于动态显示知识星图标题。',
    )
    teacher: str = Field(
        description='授课教师。',
    )
    description: str = Field(
        description='课程简介。',
    )
    chapter: str = Field(
        description='当前章节说明。',
    )
    lessons: int = Field(
        description='课程总课时数。',
    )
    completedLessons: int = Field(
        description='已完成课时数。',
    )
    progress: int = Field(
        description='由完成课时/总课时计算的百分比；总课时为 0 时为 0。',
    )
    progressSource: str = Field(
        description='进度来源，当前固定为 lessonCompletion；不等同于能力评分。',
    )
    cover: str = Field(
        description='前端课程封面的资源名称。',
    )
    archived: bool = Field(
        description='是否已归档。',
    )
    sample: bool = Field(
        description='是否为用户显式创建的样例数据。',
    )
    createdAt: str = Field(
        description='创建时间，UTC ISO 8601 格式。',
    )


class CourseListItem(CourseOut):
    materialCount: int = Field(
        description='该课程的资料数量。',
    )


class CourseCounts(BaseModel):
    all: int = Field(
        description='账号下全部课程数量，不受关键词过滤影响。',
    )
    active: int = Field(
        description='未归档课程数量。',
    )
    archived: int = Field(
        description='已归档课程数量。',
    )


class CoursePage(Page[CourseListItem]):
    counts: CourseCounts = Field(
        description='课程筛选页签的账号级数量。',
    )


class MaterialOut(BaseModel):
    id: str = Field(
        description='资料 ID。',
    )
    courseId: str = Field(
        description='所属课程 ID。',
    )
    name: str = Field(
        description='文件名称。',
    )
    type: str = Field(
        description='资料类型：TXT、MD 或 PDF。',
    )
    sizeBytes: int = Field(
        description='原文件大小，单位字节。',
    )
    size: str = Field(
        description='供页面展示的文件大小文字。',
    )
    created: str = Field(
        description='创建日期，YYYY-MM-DD，UTC 日期。',
    )
    createdAt: str = Field(
        description='创建时间，UTC ISO 8601。',
    )
    sample: bool = Field(
        description='是否为样例资料。',
    )
    parseStatus: Literal['ready', 'no_text'] = Field(
        description='ready 可文本检索；no_text 没有可提取正文，如扫描 PDF。',
    )


class MaterialDetailOut(MaterialOut):
    text: str = Field(
        description='解析后的文字正文，供阅读、搜索与引用定位；不是原文件二进制。',
    )


class SubtaskOut(BaseModel):
    id: str = Field(description='小任务 ID。')
    title: str = Field(description='小任务标题。')
    done: bool = Field(description='当前小任务是否完成。')


class TaskOut(BaseModel):
    subtasks: list[SubtaskOut] = Field(description='有序小任务清单；exercise 为空数组。')
    kind: Literal['custom', 'exercise'] = Field(description='计划类型。')
    courseId: str | None = Field(description='关联课程 ID。')
    questionIds: list[str] = Field(description='计划中需要提交的题目。')
    completedQuestionIds: list[str] = Field(description='此计划内已提交题目，不读取历史作答。')
    reminderAt: str | None = Field(description='UTC ISO8601 提醒时间；null 不提醒。')
    completedAt: str | None = Field(description='最近完成时间，UTC ISO8601。')
    id: str = Field(
        description='复习任务 ID。',
    )
    title: str = Field(
        description='任务标题。',
    )
    description: str = Field(
        description='任务说明。',
    )
    minutes: int = Field(
        description='计划用时，单位分钟。',
    )
    date: str = Field(
        description='计划日期 YYYY-MM-DD；未指定日期时为空字符串。',
    )
    done: bool = Field(
        description='是否已完成。',
    )
    target: str = Field(
        description='点击开始学习时跳转的站内 /app/ 路径。',
    )
    createdAt: str = Field(
        description='创建时间，UTC ISO 8601。',
    )


class QuestionOut(BaseModel):
    knowledgeNodeId: str | None = Field(default=None, description='稳定知识点 ID；旧题或未关联题为 null。')
    sourceEvidence: QuestionEvidence | None = Field(default=None, description='原文证据；仅已判题的快照中返回。')
    id: str = Field(
        description='题目 ID。',
    )
    courseId: str = Field(
        description='所属课程 ID。',
    )
    title: str = Field(
        description='题干。',
    )
    options: list[str] = Field(
        description='选项数组，下标从 0 开始。查询题库时不返回答案。',
    )
    point: str = Field(
        description='关联知识点名称，用于报告和知识图谱统计。',
    )
    source: str | None = Field(
        description='来源资料 ID；无来源或已删除时可为 null。',
    )
    sample: bool = Field(
        description='是否为样例题。',
    )


class GradedQuestionOut(QuestionOut):
    answer: int = Field(
        description='正确选项下标，从 0 开始；仅出现在判题结果或历史错题快照。',
    )
    explanation: str = Field(
        description='答案解析。',
    )


class AttemptOut(BaseModel):
    knowledgeNodeId: str | None = Field(default=None, description='作答时关联的稳定知识点 ID。')
    sourceEvidence: QuestionEvidence | None = Field(default=None, description='作答时保存的原文引用。')
    title: str = Field(description='作答时的题干快照，供历史列表展示，不返回正确答案。')
    point: str = Field(description='本次作答的知识点名称。')
    id: str = Field(
        description='作答记录 ID。',
    )
    courseId: str = Field(
        description='课程 ID。',
    )
    questionId: str = Field(
        description='题目 ID。',
    )
    submissionId: str = Field(
        description='客户端为本次提交生成的幂等键，网络重试保持不变。',
    )
    choice: int = Field(
        description='选择的选项下标，从 0 开始。',
    )
    correct: bool = Field(
        description='服务端判题结果，不能由前端直接指定。',
    )
    seconds: int = Field(
        description='客户端记录的答题秒数；不作为防作弊考试时间。',
    )
    date: str = Field(
        description='服务端记录的作答时间，UTC ISO 8601。',
    )


class GradeOut(BaseModel):
    knowledgeNodeId: str | None = Field(default=None, description='本次作答关联的稳定知识点 ID。')
    sourceEvidence: QuestionEvidence | None = Field(default=None, description='题目快照中保存的原文证据。')
    task: TaskOut | None = Field(default=None, description='提交后最新计划及进度；普通作答为 null。')
    attempt: AttemptOut = Field(
        description='本次作答，重复提交返回原记录。',
    )
    answer: int = Field(
        description='本次判题快照中的正确选项下标。',
    )
    explanation: str = Field(
        description='本次判题快照中的解析。',
    )
    source: str | None = Field(
        description='本次快照中的来源资料 ID，可能已被删除。',
    )
    point: str = Field(
        description='知识点名称。',
    )


class MistakeOut(GradedQuestionOut):
    reviewed: bool = Field(
        description='最近一次作答是否正确。曾经答错但之后答对的题仍可查看。',
    )
    wrongCount: int = Field(
        description='该题历史答错次数。',
    )
    lastWrong: AttemptOut = Field(
        description='最近一次错误作答。',
    )
    lastAttempt: AttemptOut = Field(
        description='最近一次作答，可能正确或错误。',
    )


class PlanMistakeOut(BaseModel):
    created: bool = Field(
        description='true 表示新建，false 表示返回已有待完成任务。',
    )
    task: TaskOut = Field(
        description='对应错题的复习任务。',
    )


class PointReport(BaseModel):
    courseId: str = Field(description='本行知识点所属课程。')
    knowledgeNodeId: str | None = Field(default=None, description='该课程中仍存在的知识点 ID。旧记录可为 null。')
    name: str = Field(
        description='知识点名称。',
    )
    count: int = Field(
        description='该知识点作答次数。',
    )
    correct: int = Field(
        description='答对次数。',
    )
    rate: int = Field(
        description='正确率百分比，空记录为 0。',
    )


class DayReport(BaseModel):
    key: str = Field(
        description='上海时区的日期 YYYY-MM-DD。',
    )
    label: str = Field(
        description='柱状图日期标签，例如 10/25。',
    )
    count: int = Field(
        description='该日作答次数，不是去重题目数。',
    )


class ReportOut(BaseModel):
    total: int = Field(
        description='当前筛选范围内作答总次数。',
    )
    correct: int = Field(
        description='答对次数。',
    )
    rate: int = Field(
        description='正确率百分比。',
    )
    totalSeconds: int = Field(
        description='累计答题秒数。',
    )
    minutes: int = Field(
        description='累计答题完整分钟数。',
    )
    seconds: int = Field(
        description='累计用时除以 60 的余秒。',
    )
    points: list[PointReport] = Field(
        description='按知识点汇总的统计。',
    )
    days: list[DayReport] = Field(
        description='最近七天的作答柱状图数据。',
    )
    maxCount: int = Field(
        description='柱状图的最大次数，至少为 1，避免除零。',
    )
    range: str = Field(
        description='当前统计范围 all、7 或 30。',
    )
    timezone: str = Field(
        description='按天统计使用的时区，Asia/Shanghai。',
    )


class OverviewOut(BaseModel):
    totalCourses: int = Field(
        description='全部课程数。',
    )
    activeCourses: int = Field(
        description='未归档课程数。',
    )
    materialCount: int = Field(
        description='资料总数。',
    )
    pendingTasks: int = Field(
        description='未完成任务数。',
    )
    taskCompletion: int = Field(
        description='任务完成率百分比。',
    )
    attemptCount: int = Field(
        description='作答总次数。',
    )
    correctRate: int = Field(
        description='全部作答正确率。',
    )
    totalSeconds: int = Field(
        description='累计答题秒数。',
    )
    recentTasks: list[TaskOut] = Field(
        description='最多五个未完成任务。',
    )


class NodeOut(BaseModel):
    id: str = Field(
        description='节点 ID。',
    )
    label: str = Field(
        description='节点名称。',
    )
    description: str = Field(
        description='选中后展示的知识点说明。',
    )
    x: int = Field(
        description='SVG 横坐标，范围 0～760。',
    )
    y: int = Field(
        description='SVG 纵坐标，范围 0～300。',
    )
    sample: bool = Field(
        description='是否为样例节点。',
    )


class KnowledgeNodeOut(NodeOut):
    reviewStatus: Literal['unpracticed', 'needs_review', 'retest_passed', 'practiced'] = Field(
        default='unpracticed', description='根据实际作答及变式复测结果计算的复习状态。')
    practiceCount: int = Field(
        description='该课程关联此知识点 ID 的作答次数。',
    )
    practiceRate: int = Field(
        description='该课程关联此知识点 ID 的作答正确率。',
    )


class LinkOut(BaseModel):
    source: str = Field(
        description='起点节点 ID，不是数组下标。',
    )
    target: str = Field(
        description='终点节点 ID，不是数组下标。',
    )


class KnowledgeOut(BaseModel):
    courseId: str = Field(
        description='所属课程 ID。课程名称另由课程详情接口读取。',
    )
    sample: bool = Field(
        description='非空且全部节点为样例时为 true。',
    )
    nodes: list[KnowledgeNodeOut] = Field(
        description='节点数组，直接传给 KnowledgeGraph 的 nodes。',
    )
    links: list[LinkOut] = Field(
        description='按节点 ID 描述的连接，顺序不影响关系。',
    )
    edges: list[tuple[int, int]] = Field(
        description='按本次 nodes 数组下标描述的连接，直接传给现有组件的 edges。必须与本次 nodes 配套使用。',
    )


class SourceOut(BaseModel):
    materialId: str = Field(
        description='引用资料 ID。',
    )
    name: str = Field(
        description='引用时的资料名称快照。',
    )
    excerpt: str = Field(
        description='引用原文片段。',
    )
    score: int = Field(
        description='关键词命中评分，不是模型置信度。',
    )
    start: int = Field(
        description='原正文中的 UTF-16 起始偏移，可用于 JavaScript text.slice。',
    )
    end: int = Field(
        description='UTF-16 结束偏移，不含该位置。',
    )


class ChatOut(BaseModel):
    id: str = Field(
        description='问答记录 ID。',
    )
    courseId: str = Field(
        description='所属课程 ID。',
    )
    question: str = Field(
        description='用户提问。',
    )
    text: str = Field(
        description='完整回答文字。本接口为一次性 JSON，不是 SSE。',
    )
    sources: list[SourceOut] = Field(
        description='引用的资料片段；资料不足时可为空。',
    )
    questions: list[QuestionCreate] = Field(
        default_factory=list, description='经结构校验的单选题草稿，可提交到同一课程题库导入接口。',
    )
    canPrepareQuestions: bool = Field(
        default=False, description='旧聊天是否可根据对应原文整理为单选题草稿。',
    )
    mode: Literal['retrieval', 'llm'] = Field(
        description='本次实际使用资料检索还是外部模型。',
    )
    model: str | None = Field(
        description='实际模型名称，纯检索模式为 null。',
    )
    createdAt: str = Field(
        description='保存时间，UTC ISO 8601。',
    )


class ResetOut(BaseModel):
    message: str = Field(
        description='重置结果说明。',
    )
    seedDemo: bool = Field(
        description='是否按调用方显式要求重新创建样例。',
    )


class EmailCodeOut(BaseModel):
    message: str = Field(description='SMTP 已接受邮件后的提示；不保证收件箱即时投递，请检查垃圾邮件。')
    expiresIn: int = Field(description='验证码有效秒数，当前 300 秒。')
    resendAfter: int = Field(description='同一邮箱再次发送需等待的秒数，当前 60 秒。')


class PasswordResetCodeOut(EmailCodeOut):
    message: str = Field(description='已受理请求。该提示不表示账号是否存在，也不确认邮件投递结果。')


class ErrorDetail(BaseModel):
    field: str = Field(
        description='参数位置，例如 body.password 或 query.limit。',
    )
    message: str = Field(
        description='参数校验错误说明，不回显密码输入。',
    )


class ErrorInfo(BaseModel):
    code: str = Field(
        description='错误代码，例如 http_401、validation_error。',
    )
    message: str = Field(
        description='供页面展示的错误说明。',
    )
    details: list[ErrorDetail] | None = Field(
        default=None,
        description='422 时的字段错误明细；其他错误可以没有该字段。',
    )


class ErrorOut(BaseModel):
    error: ErrorInfo = Field(
        description='结构化错误。',
    )
    requestId: str = Field(
        description='本次请求标识，便于在服务端日志定位。',
    )


ERROR_RESPONSES = {
    400: {'model': ErrorOut, 'description': '验证码错误、已过期、已使用或尝试次数达到上限。'},
    401: {'model': ErrorOut, 'description': '未登录、账号密码错误或会话过期。'},
    403: {'model': ErrorOut, 'description': '该入口被配置关闭。'},
    404: {'model': ErrorOut, 'description': '资源不存在或不属于当前账号。'},
    409: {'model': ErrorOut, 'description': '重复资源、幂等键冲突或资源数量达到上限。'},
    413: {'model': ErrorOut, 'description': '请求、单文件或账号存储总量超限。'},
    415: {'model': ErrorOut, 'description': '不支持的资料格式。'},
    422: {'model': ErrorOut, 'description': '请求参数校验失败，查看 error.details 或 error.message。'},
    429: {'model': ErrorOut, 'description': '操作过于频繁，请按 Retry-After 提示稍后重试。'},
    502: {'model': ErrorOut, 'description': '外部模型服务响应错误。'},
    503: {'model': ErrorOut, 'description': '数据库暂不可用，请检查数据库连接和服务日志。'},
    504: {'model': ErrorOut, 'description': '外部模型服务超时。'}
}

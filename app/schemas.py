"""沿用 Vue 2 的 camelCase 字段；未知字段报错，禁止前端伪造判分和日期。"""
from datetime import date, datetime, timezone
from email.headerregistry import Address
from email.errors import HeaderParseError
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=30)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Credentials(Input):
    email: Annotated[str, Field(min_length=3, max_length=100)] = Field(
        description='邮箱地址。账号入口使用登录邮箱；个人资料中的 email 仅为联系邮箱，不修改登录账号。',
        examples=['student@example.com'],
    )
    # 密码保留空格，不能被自动 trim。
    password: Annotated[str, StringConstraints(min_length=8, max_length=128, strip_whitespace=False)] = Field(
        description='账号密码，8～128 位，保留首尾空格。仅通过 HTTPS 或本地连接提交，不保存到前端缓存。',
        examples=['StudyDemo123!'],
    )

    @field_validator('email')
    @classmethod
    def email_valid(cls, value):
        """验证邮箱格式并转为小写，统一登录账号查询规则。"""
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('邮箱格式不正确')
        try:
            address = Address(addr_spec=value)
            if not address.username or not address.domain or address.addr_spec != value:
                raise ValueError()
        except (ValueError, HeaderParseError):
            raise ValueError('请输入单个有效邮箱地址') from None
        return value.lower()


class Register(Credentials):
    verificationCode: Annotated[str, Field(pattern=r'^[0-9]{6}$')] = Field(
        description='发到同一邮箱的 6 位注册验证码，5 分钟有效，只能成功使用一次；保留开头的 0。',
        examples=['012345'],
    )
    name: Name = Field(
        default='同学',
        description='显示名称，供个人资料、侧边栏或课程标题展示。',
    )
    seedDemo: StrictBool = Field(
        default=False,
        description='是否显式导入样例课程、资料、题库和图谱；默认 false，正式账号不会自动生成成绩。',
    )


class EmailCodeRequest(Input):
    email: Annotated[str, Field(min_length=3, max_length=100)] = Field(
        description='接收注册验证码的邮箱。不能是已注册邮箱；服务端会转为小写。',
        examples=['student@example.com'],
    )

    @field_validator('email')
    @classmethod
    def email_valid(cls, value):
        """复用账号邮箱格式校验，保证发信与注册使用相同规范化规则。"""
        return Credentials.email_valid(value)


class Patch(Input):
    @model_validator(mode='after')
    def reject_null(self):
        """PATCH 请求拒绝 null 和空对象；需要保持原值时应省略该字段。"""
        for key in self.model_fields_set:
            if getattr(self, key) is None:
                raise ValueError(f'{key} 不接受 null；不修改时请省略该字段')
        if not self.model_fields_set:
            raise ValueError('至少提供一个修改字段')
        return self


class CourseCreate(Input):
    name: Name = Field(
        description='显示名称，供个人资料、侧边栏或课程标题展示。',
    )
    teacher: Annotated[str, Field(max_length=20)] = Field(
        default='未设置',
        description='授课教师；不填写时使用默认文字。',
    )
    description: Annotated[str, Field(max_length=180)] = Field(
        default='',
        description='内容说明，供对应详情区域展示。',
    )
    chapter: Annotated[str, Field(max_length=80)] = Field(
        default='开始整理你的课程知识',
        description='章节说明，例如第三章·特征值与特征向量。',
    )
    lessons: Annotated[int, Field(ge=0, le=10000, strict=True)] = Field(
        default=0,
        description='课程总课时数，整数 0～10000；不能小于已完成课时。',
    )
    cover: Literal['linear', 'structures', 'english'] = Field(
        default='structures',
        description='前端封面资源名：linear、structures 或 english；不是图片 URL。',
    )


class CoursePatch(Patch):
    name: Name | None = Field(
        default=None,
        description='显示名称，供个人资料、侧边栏或课程标题展示。',
    )
    teacher: Annotated[str, Field(max_length=20)] | None = Field(
        default=None,
        description='授课教师；不填写时使用默认文字。',
    )
    description: Annotated[str, Field(max_length=180)] | None = Field(
        default=None,
        description='内容说明，供对应详情区域展示。',
    )
    chapter: Annotated[str, Field(max_length=80)] | None = Field(
        default=None,
        description='章节说明，例如第三章·特征值与特征向量。',
    )
    lessons: Annotated[int, Field(ge=0, le=10000, strict=True)] | None = Field(
        default=None,
        description='课程总课时数，整数 0～10000；不能小于已完成课时。',
    )
    cover: Literal['linear', 'structures', 'english'] | None = Field(
        default=None,
        description='前端封面资源名：linear、structures 或 english；不是图片 URL。',
    )
    archived: StrictBool | None = Field(
        default=None,
        description='是否归档课程，true 为归档，false 为恢复；不会删除课程资料。',
    )
    completedLessons: Annotated[int, Field(ge=0, le=10000, strict=True)] | None = Field(
        default=None,
        description='已完成课时数，整数 0～总课时；后端据此计算 progress，前端不能直接指定百分比。',
    )


class ProfilePatch(Patch):
    name: Annotated[str, Field(min_length=1, max_length=20)] | None = Field(
        default=None,
        description='显示名称，供个人资料、侧边栏或课程标题展示。',
    )
    major: Annotated[str, Field(max_length=30)] | None = Field(
        default=None,
        description='大学专业名称，可为空字符串。',
    )
    grade: Literal['大一', '大二', '大三', '大四', '研究生', '其他'] | None = Field(
        default=None,
        description='年级，支持大一、大二、大三、大四、研究生或其他。',
    )
    email: Annotated[str, Field(max_length=100)] | None = Field(
        default=None,
        description='邮箱地址。账号入口使用登录邮箱；个人资料中的 email 仅为联系邮箱，不修改登录账号。',
        examples=['student@example.com'],
    )
    reducedMotion: StrictBool | None = Field(
        default=None,
        description='是否减少页面动画；true 时前端应使用静态或简化效果。',
    )
    reminders: StrictBool | None = Field(
        default=None,
        description='是否显示学习提醒。',
    )

    @field_validator('email')
    @classmethod
    def contact_email_valid(cls, value):
        """验证联系邮箱，允许空字符串表示暂未设置。"""
        if value and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('联系邮箱格式不正确')
        return value


class TaskFields(Input):
    @field_validator('reminderAt', check_fields=False)
    @classmethod
    def reminder_valid(cls, value):
        """要求明确时区，并归一为数据库使用的 UTC。"""
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('提醒时间必须包含时区')
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @field_validator('date', check_fields=False)
    @classmethod
    def date_valid(cls, value):
        """校验日期为规范 YYYY-MM-DD，允许空字符串表示未指定日期。"""
        if value:
            try:
                parsed = date.fromisoformat(value)
                if value != parsed.isoformat():
                    raise ValueError()
            except ValueError:
                raise ValueError('日期应为 YYYY-MM-DD；不设日期传空字符串')
        return value

    @field_validator('target', check_fields=False)
    @classmethod
    def target_valid(cls, value):
        """限制任务跳转目标为允许的站内 /app/ 路径，拒绝外部 URL。"""
        if value is not None and not re.fullmatch(r'/app/(?:overview|courses(?:/[A-Za-z0-9_-]+)?|studio|knowledge|practice|mistakes|reports|plan|settings)(?:\?[A-Za-z0-9_=&%.-]+)?', value):
            raise ValueError('target 必须是站内 /app/ 页面路径')
        return value


class SubtaskCreate(Input):
    title: Annotated[str, Field(min_length=1, max_length=200)] = Field(description='小任务标题，去除首尾空白后 1～200 字。')
    done: Literal[False] = Field(default=False, description='新增小任务固定未完成；勾选请使用独立接口。')


class SubtaskEdit(Input):
    id: Annotated[str, Field(min_length=1, max_length=32)] | None = Field(default=None, description='已有小任务 ID；新增时不传。')
    title: Annotated[str, Field(min_length=1, max_length=200)] = Field(description='小任务标题，1～200 字。')
    done: StrictBool | None = Field(default=None, description='兼容编辑快照；已有项忽略此值，新增项仅允许 false。')


class SubtaskToggle(Input):
    done: StrictBool = Field(description='小任务的目标勾选状态；父任务完成状态由所有小任务聚合。')


class TaskCreate(TaskFields):
    subtasks: list[SubtaskCreate] | None = Field(default=None, min_length=1, max_length=100, description='custom 小任务清单，1～100 项；省略时兼容创建同标题单项。exercise 禁止提供。')

    @model_validator(mode='after')
    def validate_subtasks(self):
        """Reject explicit null and exercise checklists, and forbid completion bypass."""
        if 'subtasks' in self.model_fields_set and (self.subtasks is None or self.kind == 'exercise'):
            raise ValueError('仅自定义计划可提供非空小任务清单')
        if self.kind == 'custom' and self.done:
            raise ValueError('自定义计划只能由小任务聚合完成')
        return self

    kind: Literal['custom', 'exercise'] = Field(default='custom', description='custom 勾选全部小任务完成；exercise 完成所有题目提交。')
    courseId: Annotated[str, Field(min_length=1, max_length=64)] | None = Field(default=None, description='习题所属本人课程 ID。')
    questionIds: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=1000, description='同一课程内选定题目 ID，习题计划不能为空。')
    reminderAt: datetime | None = Field(default=None, description='含时区 ISO8601 提醒时间；null 不提醒。')
    title: Annotated[str, Field(min_length=1, max_length=60)] = Field(
        description='标题；课程题库中表示题干，复习计划中表示任务名称。',
    )
    description: Annotated[str, Field(max_length=200)] = Field(
        default='',
        description='内容说明，供对应详情区域展示。',
    )
    minutes: Annotated[int, Field(ge=1, le=240, strict=True)] = Field(
        default=15,
        description='预计复习分钟数，整数 1～240，不是实际已学习时间。',
    )
    date: Annotated[str, Field(max_length=10)] = Field(
        default='',
        description='计划日期，YYYY-MM-DD；空字符串表示未指定日期。',
    )
    done: StrictBool = Field(
        default=False,
        description='兼容旧客户端，仅允许 false；完成状态由小任务或作答聚合。',
    )
    target: Annotated[str, Field(max_length=200)] = Field(
        default='/app/practice',
        description='学习跳转路径，只允许本站 /app/ 页面，例如 /app/practice?question=题目ID。',
    )


class TaskPatch(TaskFields, Patch):
    subtasks: list[SubtaskEdit] | None = Field(default=None, min_length=1, max_length=100, description='原子替换清单并保留顺序：已有 ID 保留数据库 done，缺失 ID 删除，新增项不传 ID 且未完成。')
    @model_validator(mode='after')
    def reject_null(self):
        """允许显式清空提醒时间，其余必需字段不接受 null。"""
        for key in self.model_fields_set:
            if getattr(self, key) is None and key not in {'reminderAt', 'courseId'}:
                raise ValueError(f'{key} 不接受 null')
        if not self.model_fields_set:
            raise ValueError('至少提供一个修改字段')
        return self

    kind: Literal['custom', 'exercise'] | None = Field(default=None, description='计划类型；创建后不可更改。')
    courseId: Annotated[str, Field(min_length=1, max_length=64)] | None = Field(default=None, description='所属课程；创建后不可更改。')
    questionIds: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(default=None, max_length=1000, description='选定题目；创建后不可更改。')
    reminderAt: datetime | None = Field(default=None, description='含时区提醒时间；null 清除提醒。')
    title: Annotated[str, Field(min_length=1, max_length=60)] | None = Field(
        default=None,
        description='标题；课程题库中表示题干，复习计划中表示任务名称。',
    )
    description: Annotated[str, Field(max_length=200)] | None = Field(
        default=None,
        description='内容说明，供对应详情区域展示。',
    )
    minutes: Annotated[int, Field(ge=1, le=240, strict=True)] | None = Field(
        default=None,
        description='预计复习分钟数，整数 1～240，不是实际已学习时间。',
    )
    date: Annotated[str, Field(max_length=10)] | None = Field(
        default=None,
        description='计划日期，YYYY-MM-DD；空字符串表示未指定日期。',
    )
    done: StrictBool | None = Field(
        default=None,
        description='保留旧字段用于明确报错；父任务禁止直接修改 done，返回 422。',
    )
    target: Annotated[str, Field(max_length=200)] | None = Field(
        default=None,
        description='学习跳转路径，只允许本站 /app/ 页面，例如 /app/practice?question=题目ID。',
    )


class AttemptCreate(Input):
    taskId: Annotated[str, Field(min_length=1, max_length=64)] | None = Field(default=None, description='可选习题计划 ID；仅本次计划内提交计入进度。')
    questionId: Annotated[str, Field(min_length=1, max_length=80)] = Field(
        description='题目 ID，必须来自当前账号可访问的题库。',
    )
    choice: Annotated[int, Field(ge=0, le=20, strict=True)] = Field(
        description='用户选择的选项下标，从 0 开始；必须小于该题选项数量。',
    )
    seconds: Annotated[int, Field(ge=0, le=86400, strict=True)] = Field(
        default=0,
        description='本次答题耗时，整数秒，0～86400；由客户端计时，不是防作弊考试时间。',
    )
    submissionId: Annotated[str, Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')] = Field(
        description='本次提交的唯一幂等键，8～100 位字母/数字/下划线/连字符。网络重试沿用；新一次作答使用新值。',
        examples=['attempt_demo_0001'],
    )


class Ask(Input):
    courseId: Annotated[str, Field(min_length=1, max_length=80)] = Field(
        description='课程 ID，必须属于当前登录账号；使用课程接口返回的 id。',
    )
    question: Annotated[str, Field(min_length=2, max_length=2000)] = Field(
        description='学习问题，2～2000 字；仅检索指定课程下可解析的文字资料。',
        examples=['对角矩阵的特征值怎么求？'],
    )


class Reset(Input):
    confirm: Literal['RESET_MY_LEARNING_DATA'] = Field(
        description='清空本人学习数据的显式确认文字，必须为 RESET_MY_LEARNING_DATA；账号和偏好保留。',
    )
    seedDemo: StrictBool = Field(
        default=False,
        description='是否显式导入样例课程、资料、题库和图谱；默认 false，正式账号不会自动生成成绩。',
    )


class QuestionCreate(Input):
    title: Annotated[str, Field(min_length=1, max_length=500)] = Field(
        description='标题；课程题库中表示题干，复习计划中表示任务名称。',
    )
    options: Annotated[list[Annotated[str, Field(min_length=1, max_length=500)]], Field(min_length=2, max_length=6)] = Field(
        description='2～6 个不同的选项文字，下标从 0 开始；每项 1～500 字。',
    )
    answer: Annotated[int, Field(ge=0, lt=6, strict=True)] = Field(
        description='正确选项的下标，从 0 开始，必须小于 options 的长度；题库创建仅面向本人维护，不是正式考试权限。',
    )
    explanation: Annotated[str, Field(min_length=1, max_length=5000)] = Field(
        description='答案解析，提交作答后返回给前端。',
    )
    point: Annotated[str, Field(min_length=1, max_length=80)] = Field(
        description='知识点名称，例如特征值；用于按知识点汇总报告与图谱统计。',
    )
    source: Annotated[str, Field(max_length=64)] | None = Field(
        default=None,
        description='可选来源资料 ID，必须属于同一课程；不关联资料时省略或传 null。',
    )

    @model_validator(mode='after')
    def answer_exists(self):
        """检查正确答案下标在选项范围内，并拒绝重复选项。"""
        if self.answer >= len(self.options):
            raise ValueError('正确答案超出选项范围')
        if len(set(self.options)) != len(self.options):
            raise ValueError('选项不能重复')
        return self


class NodeCreate(Input):
    label: Annotated[str, Field(min_length=1, max_length=80)] = Field(
        description='知识点节点名称，显示在 SVG 节点旁。',
        examples=['特征值'],
    )
    description: Annotated[str, Field(max_length=5000)] = Field(
        default='',
        description='内容说明，供对应详情区域展示。',
    )
    x: Annotated[int, Field(ge=0, le=760, strict=True)] = Field(
        default=100,
        description='知识点在 SVG viewBox="0 0 760 300" 中的横坐标，整数 0～760。',
        examples=[385],
    )
    y: Annotated[int, Field(ge=0, le=300, strict=True)] = Field(
        default=100,
        description='知识点在 SVG viewBox="0 0 760 300" 中的纵坐标，整数 0～300。',
        examples=[105],
    )


class EdgeCreate(Input):
    source: Annotated[str, Field(min_length=1, max_length=64)] = Field(
        description='起点知识节点 ID，必须属于当前课程，不是 nodes 的数组下标。',
    )
    target: Annotated[str, Field(min_length=1, max_length=64)] = Field(
        description='终点知识节点 ID，必须属于当前课程，不能与 source 相同。',
    )

    @model_validator(mode='after')
    def no_self_edge(self):
        """拒绝知识点节点与自身相连。"""
        if self.source == self.target:
            raise ValueError('不支持节点自连')
        return self


class ProfileOut(BaseModel):
    name: str = Field(
        description='显示名称，供个人资料、侧边栏或课程标题展示。',
    )
    major: str = Field(
        description='大学专业名称，可为空字符串。',
    )
    grade: str = Field(
        description='年级，支持大一、大二、大三、大四、研究生或其他。',
    )
    email: str = Field(
        description='邮箱地址。账号入口使用登录邮箱；个人资料中的 email 仅为联系邮箱，不修改登录账号。',
        examples=['student@example.com'],
    )
    reducedMotion: bool = Field(
        description='是否减少页面动画；true 时前端应使用静态或简化效果。',
    )
    reminders: bool = Field(
        description='是否显示学习提醒。',
    )


class UserOut(BaseModel):
    emailVerified: bool = Field(description='该账号是否通过了注册邮箱验证码；历史账号和体验账号可能为 false。')
    id: str = Field(
        description='服务端生成的用户唯一 ID。',
    )
    email: str = Field(
        description='邮箱地址。账号入口使用登录邮箱；个人资料中的 email 仅为联系邮箱，不修改登录账号。',
        examples=['student@example.com'],
    )
    isGuest: bool = Field(
        description='是否为独立体验账号，体验会话有效期 24 小时。',
    )
    profile: ProfileOut = Field(
        description='当前用户的展示资料和界面偏好，不包含密码哈希。',
    )


class AuthOut(BaseModel):
    accessToken: str = Field(
        description='可撤销的随机会话令牌；后续通过 Authorization: Bearer <令牌> 发送，不是 JWT。',
    )
    tokenType: str = Field(
        default='bearer',
        description='令牌类型，固定为 bearer。',
    )
    expiresAt: str = Field(
        description='令牌到期时间，UTC ISO 8601；过期后重新登录。',
    )
    user: UserOut = Field(
        description='当前登录用户，含用户 ID、登录邮箱和展示资料，不包含密码哈希。',
    )

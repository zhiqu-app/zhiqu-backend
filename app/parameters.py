"""可复用的路径、查询参数定义；description 会进入 Swagger/OpenAPI。"""
from typing import Annotated, Literal

from fastapi import Path, Query

CourseId = Annotated[str, Path(min_length=1, max_length=80, description='课程 ID。使用课程创建/列表接口返回的 id，不是课程名称。')]
MaterialId = Annotated[str, Path(min_length=1, max_length=80, description='资料 ID。由资料上传或列表接口返回，必须属于当前账号。')]
QuestionId = Annotated[str, Path(min_length=1, max_length=80, description='题目 ID。由题库接口返回，必须属于当前账号。')]
TaskId = Annotated[str, Path(min_length=1, max_length=80, description='复习任务 ID。由任务创建或列表接口返回。')]
RequiredCourse = Annotated[str, Query(min_length=1, max_length=80, description='必填课程 ID，仅查询该课程的数据；体验账号可使用 linear，真实账号使用接口返回的 id。')]
OptionalCourse = Annotated[str | None, Query(max_length=80, description='可选课程 ID；不传时查询当前账号全部课程的数据。')]
Search = Annotated[str, Query(max_length=100, description='搜索关键词；课程匹配名称/简介，资料匹配文件名。不传或空字符串表示不按关键词过滤。', examples=['线性代数'])]
CourseStatus = Annotated[Literal['all', 'active', 'archived'], Query(description='课程状态：all 全部，active 未归档，archived 已归档。')]
TaskStatus = Annotated[Literal['all', 'pending', 'done', 'today'], Query(description='任务状态：all 全部，pending 未完成，done 已完成，today 上海时区今天到期的任务。')]
MistakeStatus = Annotated[Literal['all', 'pending', 'reviewed'], Query(description='错题状态：all 曾答错的全部题，pending 最近一次仍答错，reviewed 最近一次已答对。')]
ReportRange = Annotated[Literal['all', '7', '30'], Query(description='统计范围：all 全部记录，7 最近七天，30 最近三十天。', examples=['7'])]
InlinePreview = Annotated[bool, Query(description='true 时 PDF 以内嵌方式返回，可用 Blob URL 在 iframe 预览；其他文件或 false 时下载。')]

SubtaskId = Annotated[str, Path(min_length=1, max_length=32, description='小任务 ID；必须属于路径中的父任务和本人账号。')]

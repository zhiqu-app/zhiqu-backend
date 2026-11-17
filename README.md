# 知序独立后端：FastAPI + SQLAlchemy + MySQL 8.0

这份目录独立于 Vue 前端，提供 **59 个 API 操作、21 张关系表、Alembic 迁移**。业务字段沿用前端 camelCase。正式注册默认空数据；只有显式体验入口才创建样例。

先阅读以下独立文档：

- [QQ 邮箱验证码注册与授权码配置](docs/邮箱注册说明.md)

- [MySQL 8.0 配置与启动步骤](docs/MySQL8配置说明.md)
- [从登录到知识星图的接口调用教程](docs/使用指南.md)
- [完整接口与参数说明](docs/接口参考.md)
- [每个函数的作用与源码位置](docs/函数参考.md)
- [数据库表结构](docs/数据库表结构.md)
- [前端每个页面如何联调](docs/前端联调说明.md)
- [本轮验证结果与限制](docs/验证说明.md)

## 快速启动

在本目录打开 PowerShell（交付后为 `D:/zhiqu-backend/backend`）：

```powershell
.\.venv\Scripts\activate  #如果出现错误可以确保使用虚拟环境
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

按数据库配置文档创建专用库、账号并填写 `.env`。然后：

```powershell
.\.venv\Scripts\python.exe scripts\check_database.py
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/docs，按邮箱注册文档配置 SMTP 后先发送验证码，再注册账号，最后点 Authorize 输入注册/登录返回的 accessToken。
API 前缀地址 http://127.0.0.1:8000/api/v1 会显示文档与健康检查入口。服务启动要求真实 MySQL 连接和迁移正确；不会静默改用 SQLite。

找回密码复用注册邮件的 SMTP 配置和 `EMAIL_CODE_SECRET`。更新部署时先执行 `python -m alembic upgrade head`，再重启后端并发布前端。

`0010` 为题目、作答增加知识点 ID，并为题目保存原文引用。同账号同课程内只有唯一同名节点时才会自动关联旧记录。未关联的旧题可以在题库中手动补绑，已有作答仍保留原名称快照。题库中的「AI 出题」需要配置模型，生成结果由用户审核后入库。错题本可安排同知识点的未做变式题，默认两天后提醒。

- `POST /api/v1/auth/password-reset/code` 接收 `email`。只有已验证登录邮箱的正式账号会收到邮件。响应提示受理，验证码有效 5 分钟，同邮箱 60 秒后可重发。
- `POST /api/v1/auth/password-reset` 接收 `email`、`verificationCode` 和新 `password`。成功返回 `204`，验证码只能使用一次，旧登录会话会被撤销。

## AI 研习室图片提问

前端选定课程后，可以在「课程答疑」上传截图、题目图或讲义照片，也可以在输入框粘贴截图。支持单张静态 PNG、JPEG、WebP，最多 5 MB，边长不超过 4096 像素。可不填问题直接发送。图片会发送给已配置的图片模型，问答历史保存问题、回答与原文引用。

已配置 DeepSeek 官方 API 时，后端默认用 `deepseek-flash` 识别图片。其他兼容 Chat Completions 的服务需要在 `.env` 配置支持图片输入的 `AI_VISION_MODEL`。`AI_VISION_BASE_URL`、`AI_VISION_API_KEY` 可选。使用不同的接口地址时填写专用密钥。

已有部署安装更新后的 `requirements.lock.txt`，重启后端并发布前端即可。图片提问复用现有问答历史表，无需为这个功能执行数据库迁移。可在 Swagger 调试 `POST /api/v1/assistant/ask-image`。

## 研习室练习题入库

从课程资料生成的单选题可在 AI 聊天逐题或批量加入当前课程题库。题干、选项、答案解析、知识点与原文证据随题保存，导入会跳过重复题。带原文引用的旧聊天可以先点击「整理成题库题目」。

本功能的 Alembic 版本为 `0012`。升级会在问答记录中添加可空的草稿字段，既有聊天正文、引用和题库数据继续保留。

## 复习教练建议历史

复习教练生成的建议会保存到本人账号。页面上的「建议历史」按时间倒序列出课程、复习目标、薄弱知识点和当时推荐的原文与练习。学习数据导出会包含建议历史。

已有部署需要执行 `python -m alembic upgrade head` 升级至 `0013`，随后重启后端并发布前端。

## 文件职责

```text
app/main.py          应用入口、CORS、错误处理、启动检查
app/config.py        .env 配置和安全构造 MySQL URL
app/database.py      SQLAlchemy 引擎、连接池和 Session 工厂
app/models.py        用户、会话、课程、资料等 ORM 表
app/dependencies.py  每请求事务、鉴权、所有权、分页和限流依赖
app/parameters.py    Path/Query 参数类型和 description
app/schemas.py       请求模型、校验和认证响应模型
app/responses.py     有字段说明的响应模型和错误结构
app/auth.py          注册、登录、退出和个人资料接口
app/resources.py     课程、文件资料、复习任务接口
app/study.py         题库、作答、错题、报告和星图接口
app/assistant.py     课程资料问答和问答历史接口
app/services.py      文本/PDF 解析、引用检索和可选外部模型
app/data_api.py      本人学习记录导出与重置
app/serializers.py   ORM 记录转换为前端字段
migrations/          Alembic 表结构版本
sql/                 手工建库与账号权限脚本
scripts/             连接诊断、自动导出接口文档
docs/                独立接口、函数、数据库及联调文档
examples/            Vue 2 请求封装和完整调用脚本
tests/               鉴权、隔离、持久化、迁移和文档契约检查
```


默认回归测试显式注入独立测试引擎，并不等于真实 MySQL 联测；生产路径只使用 MySQL。数据库真实联测的设置和结果见验证文档。

此服务提供个人学习数据、邮箱找回密码和带原文引用的 AI 出题草稿。问答默认资料检索，配置模型后才请求外部服务。教师考试管理、扫描 PDF 的 OCR 和自动知识抽取仍待开发。数据库完整备份需使用 MySQL 的备份工具。

## 个人星图配置（2.3.0，历史）
此版本的账号级模板流程已停用，由下述 2.4.0 课程级手动配置替代；历史课程与资料保留。


## 课程知识星图（2.4.0）
流程调整为先创建课程、再手动配置该课程的知识点和关系，可选关联复习任务；不再要求登录时配置账号星图。0004 仅新增两张表，旧课程与资料保留。详见 [课程星图使用说明](docs/知识星图配置说明.md)。


## JSON 题库导入（2.5.0）
按已有课程批量追加 JSON 单选题，完整重复跳过；真实账号练习、判分、错题、报告和总览已接后端。复用现有表，迁移仍为 0004，无需改 MySQL 配置。详见 [题库使用说明](docs/题库使用说明.md) 与 [接口接入核查](docs/接口接入核查.md)。


## 本轮接口接入（2.6.0）

题库选项展示、课程分类、前端示例预览、PDF 跨会话读取、问答/历史、作答历史及其余接口入口均已接通。无新迁移。见 [使用说明](docs/课程数据使用说明.md) 和 [42 项接口清单](docs/接口接入核查.md)。

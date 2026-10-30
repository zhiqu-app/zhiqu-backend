# 知序独立后端：FastAPI + SQLAlchemy + MySQL 8.0

这份目录独立于 Vue 前端，提供当前页面需要的 **42 个 API 操作、14 张关系表、Alembic 迁移**。业务字段沿用前端 camelCase。正式注册默认空数据；只有显式体验入口才创建样例。

先阅读以下独立文档：
- [MySQL 8.0 配置与启动步骤](docs/数据库建立与配置.md)
- [从登录到知识星图的接口调用教程](docs/接口文档使用教程.md)
- [接口接入检查文档](docs/接口接入检查文档.md)
- [数据库表结构](docs/SQLAlchemy数据表.md)
- [题库pdf接口等使用说明](docs/题库pdf接口等使用说明.md)

## 快速启动

在本目录打开 PowerShell（项目完成后为 `D:zhiqu-app/backCode/backend`）：
```powershell
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

打开 http://127.0.0.1:8000/docs，按邮箱.env中变量配置 SMTP 后先发送验证码，再注册账号，最后点 Authorize 输入注册/登录返回的 accessToken。
API 前缀地址 http://127.0.0.1:8000/api/v1 会显示文档与健康检查入口。服务启动要求真实 MySQL 连接和迁移正确；不会静默改用 SQLite。

## 文件职责

```text
app/main.py          应用入口、CORS、错误处理、启动检查
app/config.py        .env 配置和安全构造 MySQL URL
app/database.py      SQLAlchemy 引擎、连接池和 Session 工厂
app/models.py        用户、会话、课程、资料等 14 张 ORM 表
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
tests/               鉴权、隔离、持久化、迁移和文档契约检查
```

## 测试代码

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

默认回归测试显式注入独立测试引擎，并不等于真实 MySQL 联测；生产路径只使用 MySQL。

此服务提供个人学习数据功能，尚不包含教师/学生考试权限、找回密码、OCR、自动知识抽取或自动出题。问答默认资料检索，配置模型后才请求外部服务。JSON 导出不是完整数据库备份。


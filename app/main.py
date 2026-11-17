from . import responses as output

from contextlib import asynccontextmanager
from contextlib import suppress
import asyncio
import logging
from pathlib import Path
import secrets

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, resources, study, assistant, data_api, course_knowledge, question_import, notifications
from .config import Settings
from .database import Database
from .dependencies import DB
from .security import RateLimiter, hash_password
from .mailer import send_password_reset_email, send_registration_email


def create_app(settings=None, *, database=None):
    """组装应用、依赖、错误处理、CORS 和业务路由；database 仅支持测试显式注入。"""
    settings = settings or Settings()
    injected = database is not None
    database = database if injected else Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        """启动时验证 MySQL 迁移版本与数据包大小，关闭时释放运行引擎。"""
        if not injected:
            config_path = Path(__file__).resolve().parents[1] / 'alembic.ini'
            expected = set(ScriptDirectory.from_config(Config(str(config_path))).get_heads())
            with database.engine.connect() as connection:
                current = set(MigrationContext.configure(connection).get_current_heads())
                if current != expected:
                    current_label = ', '.join(sorted(current)) or '未迁移'
                    expected_label = ', '.join(sorted(expected))
                    raise RuntimeError(
                        f'数据库迁移版本不匹配（当前 {current_label}，代码要求 {expected_label}）。'
                        '请在后端目录运行 python -m alembic upgrade head'
                    )
                packet = connection.scalar(text('SELECT @@max_allowed_packet'))
                if packet < 64 * 1024 * 1024:
                    raise RuntimeError('MySQL max_allowed_packet 至少设置为 64 MB 后重新连接')
        app.state.notification_hub.loop = asyncio.get_running_loop()
        scheduler = asyncio.create_task(notifications.notification_scheduler(app))
        try:
            yield
        finally:
            scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler
            await app.state.notification_hub.close()
            app.state.notification_hub.loop = None
            if not injected:
                database.close()

    app = FastAPI(title='知序 · 学习平台 API（MySQL 8.0）', version='2.6.0', lifespan=lifespan,
        description='基于 D:/zhiqu-app 实际页面设计；SQLAlchemy ORM + MySQL。正式账号默认无业务数据，'
        '示例数据只在显式体验时创建。除账号入口与健康检查外需要 Bearer。')
    app.state.settings = settings
    app.state.database = database
    app.state.notification_hub = notifications.NotificationHub()
    app.state.limiter = RateLimiter()
    app.state.mailer = send_registration_email
    app.state.password_reset_mailer = send_password_reset_email
    app.state.dummy_hash = hash_password(secrets.token_urlsafe(16))
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
        allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'], allow_headers=['Authorization','Content-Type'],
        expose_headers=['Content-Disposition','X-Request-ID','Retry-After'])

    @app.middleware(
        'http',
    )
    async def metadata(request: Request, call_next):
        """为请求附加追踪 ID，限制声明的请求大小，并给 API 响应加防嗅探与禁缓存头。"""
        request.state.request_id = secrets.token_hex(8)
        limit = 21 * 1024 * 1024 if request.headers.get('content-type','').startswith('multipart/') else 128 * 1024
        size = request.headers.get('content-length')
        try:
            rejected = size is not None and (int(size) > limit or int(size) < 0)
        except ValueError:
            rejected = True
        if rejected:
            response = JSONResponse(status_code=413, content={'error': {'code':'body_too_large','message':'请求过大'}, 'requestId':request.state.request_id})
        else:
            response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(
        StarletteHTTPException,
    )
    async def http_error(request, exc):
        """将 HTTP 错误转换为统一 error/requestId JSON，保留认证及重试响应头。"""
        return JSONResponse(status_code=exc.status_code, headers=exc.headers,
            content={'error': {'code':f'http_{exc.status_code}', 'message':exc.detail},
                     'requestId':getattr(request.state,'request_id','')})

    @app.exception_handler(
        RequestValidationError,
    )
    async def validation_error(request, exc):
        """返回 422 字段校验说明，不回显密码等原始输入值。"""
        return JSONResponse(status_code=422, content={'error': {'code':'validation_error','message':'参数校验失败',
            'details':[{'field':'.'.join(map(str,e['loc'])), 'message':e['msg']} for e in exc.errors()]},
            'requestId':getattr(request.state,'request_id','')})

    @app.exception_handler(
        SQLAlchemyError,
    )
    async def database_error(request, exc):
        # 不输出 SQL 参数、连接串或密码；请求标识用于服务端排查。
        """将 ORM/数据库故障转换为 503；服务端日志只记录类型与请求 ID，不泄露 SQL 参数。"""
        logging.getLogger('zhiqu').error('Database failure %s [%s]', type(exc).__name__, getattr(request.state,'request_id',''))
        return JSONResponse(status_code=503, content={'error':{'code':'database_unavailable','message':'数据服务暂不可用，请稍后重试'},
            'requestId':getattr(request.state,'request_id','')})

    @app.get(
        '/api/v1/health',
        tags=['服务状态'],
        summary='检查服务与数据库连接',
        response_model=output.HealthOut,
        responses={
            413: output.ERROR_RESPONSES[413],
            503: output.ERROR_RESPONSES[503],
        },
        response_description='成功，返回内容见下方 Schema；204 无响应体。',
    )
    def health(db: DB) -> dict:
        """执行轻量数据库查询，返回连接状态、服务版本和问答模式；无需登录。"""
        db.scalar(text('SELECT 1'))
        return {'status':'ok', 'database':'mysql' if not injected else 'injected-test-engine',
            'version':'2.6.0', 'assistantMode':'llm' if settings.ai_enabled else 'retrieval'}

    @app.get(
        '/api/v1',
        include_in_schema=False,
    )
    def api_index():
        """为直接访问 API 前缀的用户显示交互文档与健康检查入口，不读取业务数据。"""
        return {'message': '知序 API，请打开 docs 进行接口调试',
                'docs': '/docs', 'redoc': '/redoc', 'health': '/api/v1/health'}

    for router in [auth.router, resources.router, study.router, assistant.router, data_api.router, course_knowledge.router, question_import.router, notifications.router]:
        app.include_router(router, prefix='/api/v1')
    return app


app = create_app()

"""应用仅连接 MySQL；测试可显式注入隔离引擎，绝不静默回退。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, settings=None, *, engine=None):
        """创建 MySQL 引擎、连接池及每请求 Session 工厂；engine 参数只用于显式测试注入。"""
        self.engine = engine if engine is not None else create_engine(settings.database_url,
            pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=1800,
            pool_timeout=30, isolation_level='READ COMMITTED', hide_parameters=True,
            connect_args={'connect_timeout': 5, 'read_timeout': 60, 'write_timeout': 60, 'binary_prefix': True})
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    def close(self):
        """释放数据库引擎的连接池资源。"""
        self.engine.dispose()

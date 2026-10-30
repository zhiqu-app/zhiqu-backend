"""只读连接诊断，不打印密码，不建表。迁移请使用 Alembic。"""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from alembic.runtime.migration import MigrationContext
from app.config import Settings
from app.database import Database


def main():
    """只读检查 MySQL 连接、版本、数据包和迁移，成功返回 0，配置/连接错误返回 1。"""
    settings=Settings()
    if not settings.mysql_password:
        print('请先在 backend/.env 中填写 MYSQL_PASSWORD、数据库名和专用账号。')
        return 1
    database=Database(settings)
    try:
        with database.engine.connect() as connection:
            name,version,packet=connection.execute(text('SELECT DATABASE(),VERSION(),@@max_allowed_packet')).one()
            print(f'连接成功；库：{name}；服务版本：{version}；max_allowed_packet：{packet//1024//1024} MB')
            if 'MariaDB' in version or int(version.split('.')[0])<8:
                print('当前目标为 MySQL 8.0/8.4，请连接支持的 MySQL 服务。')
                return 1
            if packet<64*1024*1024:
                print('请由管理员将 max_allowed_packet 设置为 64 MB 后重新连接。')
                return 1
            revision=MigrationContext.configure(connection).get_current_revision()
            print('迁移版本：'+(revision or '未初始化，请执行 python -m alembic upgrade head'))
        return 0
    except SQLAlchemyError as exc:
        code=getattr(getattr(exc,'orig',None),'args',['unknown'])[0]
        print(f'连接失败（编号 {code}）。检查服务、账号、密码、库名与权限；未输出密码。')
        return 1
    finally:
        database.close()


if __name__=='__main__':
    sys.exit(main())

"""隔离浏览器联调用临时 SQLite/Uvicorn；仅在直接运行此测试脚本时启动。"""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from sqlalchemy import create_engine, event
from app.config import Settings
from app.database import Database
from app.models import Base
from app.main import create_app

def main():
    with tempfile.TemporaryDirectory(prefix='zhiqu-review-live-') as folder:
        engine = create_engine('sqlite:///' + (Path(folder) / 'review.db').as_posix(), connect_args={'check_same_thread': False})
        @event.listens_for(engine, 'connect')
        def configure(connection, _):
            connection.isolation_level = None
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
        @event.listens_for(engine, 'begin')
        def begin(connection):
            connection.exec_driver_sql('BEGIN')
        database = Database(engine=engine)
        Base.metadata.create_all(engine)
        settings = Settings(ai_base_url='', ai_api_key='', ai_model='', cors_origins=['http://127.0.0.1:5208'])
        app = create_app(settings, database=database)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8105, log_level='warning', ws='websockets'))
        @app.post('/__test__/shutdown', include_in_schema=False)
        def shutdown():
            server.should_exit = True
            return {'ok': True}
        try:
            server.run()
        finally:
            database.close()

if __name__ == '__main__':
    main()

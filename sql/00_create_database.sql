-- 在 MySQL Workbench / mysql 中以管理员执行。先将密码占位符改为自己的密码。
-- 使用独立库，避免和原先演示后端混用。
CREATE DATABASE IF NOT EXISTS zhiqu_team CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY '自己的密码';
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '自己的密码';
GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,INDEX,REFERENCES ON zhiqu_team.* TO 'root'@'localhost';
GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,INDEX,REFERENCES ON zhiqu_team.* TO 'root'@'127.0.0.1';
-- 本账号的 DDL 权限用于开发阶段执行 Alembic，生产应使用独立迁移账号，应用只留 DML。
-- IF NOT EXISTS 不会重设已有账号的密码。
-- 大文件需要 max_allowed_packet >= 64 MB，由管理员配置后重新建立应用连接。
-- 例如 MySQL 8 管理员可执行 SET PERSIST max_allowed_packet=67108864。

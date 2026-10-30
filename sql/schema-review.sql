


CREATE TABLE email_verifications (
	email VARCHAR(100) NOT NULL, 
	request_id VARCHAR(32) NOT NULL, 
	code_digest VARCHAR(64) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	requested_at DATETIME(6) NOT NULL, 
	failed_attempts INTEGER NOT NULL, 
	active BOOL NOT NULL, 
	window_started_at DATETIME(6) NOT NULL, 
	window_count INTEGER NOT NULL, 
	PRIMARY KEY (email), 
	CONSTRAINT ck_email_code_attempts CHECK (failed_attempts >= 0 AND failed_attempts <= 5), 
	CONSTRAINT ck_email_code_quota CHECK (window_count >= 0 AND window_count <= 5)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE users (
	id VARCHAR(32) NOT NULL, 
	email VARCHAR(100) NOT NULL, 
	password_hash VARCHAR(255), 
	name VARCHAR(30) NOT NULL, 
	major VARCHAR(30) NOT NULL, 
	grade VARCHAR(10) NOT NULL, 
	contact_email VARCHAR(100) NOT NULL, 
	reduced_motion BOOL NOT NULL, 
	reminders BOOL NOT NULL, 
	is_guest BOOL NOT NULL, 
	email_verified BOOL NOT NULL DEFAULT false, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE auth_sessions (
	digest VARCHAR(64) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (digest), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);


CREATE TABLE courses (
	user_id VARCHAR(32) NOT NULL, 
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(30) NOT NULL, 
	teacher VARCHAR(20) NOT NULL, 
	chapter VARCHAR(80) NOT NULL, 
	description VARCHAR(180) NOT NULL, 
	lessons INTEGER NOT NULL, 
	completed_lessons INTEGER NOT NULL, 
	cover VARCHAR(20) NOT NULL, 
	archived BOOL NOT NULL, 
	sample BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (user_id, id), 
	CONSTRAINT ck_course_lessons CHECK (lessons >= 0 AND completed_lessons >= 0 AND completed_lessons <= lessons), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_course_owner_created ON courses (user_id, created_at, id);


CREATE TABLE tasks (
	user_id VARCHAR(32) NOT NULL, 
	id VARCHAR(64) NOT NULL, 
	title VARCHAR(60) NOT NULL, 
	description VARCHAR(500) NOT NULL, 
	minutes INTEGER NOT NULL, 
	planned_date DATE, 
	done BOOL NOT NULL, 
	target VARCHAR(200) NOT NULL, 
	from_question VARCHAR(64), 
	active_key VARCHAR(100), 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (user_id, id), 
	CONSTRAINT uq_task_pending_question UNIQUE (user_id, active_key), 
	CONSTRAINT ck_task_minutes CHECK (minutes >= 1 AND minutes <= 240), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_task_owner_date ON tasks (user_id, planned_date, created_at, id);


CREATE TABLE chat_turns (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	question VARCHAR(2000) NOT NULL, 
	text TEXT NOT NULL, 
	sources JSON NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	model VARCHAR(200), 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_chat_course_date ON chat_turns (user_id, course_id, created_at, id);


CREATE TABLE course_knowledge_configs (
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	title VARCHAR(80) NOT NULL, 
	goal VARCHAR(180) NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (user_id, course_id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE knowledge_configs (
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	subject VARCHAR(30) NOT NULL, 
	level VARCHAR(20) NOT NULL, 
	goal VARCHAR(180) NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE knowledge_nodes (
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	id VARCHAR(64) NOT NULL, 
	label VARCHAR(80) NOT NULL, 
	description TEXT NOT NULL, 
	x INTEGER NOT NULL, 
	y INTEGER NOT NULL, 
	sample BOOL NOT NULL, 
	PRIMARY KEY (user_id, course_id, id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;


CREATE TABLE materials (
	user_id VARCHAR(32) NOT NULL, 
	id VARCHAR(64) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	name VARCHAR(180) NOT NULL, 
	kind VARCHAR(5) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	content LONGBLOB NOT NULL, 
	text_content LONGTEXT NOT NULL, 
	media_type VARCHAR(100) NOT NULL, 
	parse_status VARCHAR(20) NOT NULL, 
	sample BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (user_id, id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_material_course ON materials (user_id, course_id, created_at, id);


CREATE TABLE questions (
	user_id VARCHAR(32) NOT NULL, 
	id VARCHAR(64) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	options JSON NOT NULL, 
	answer INTEGER NOT NULL, 
	explanation TEXT NOT NULL, 
	point VARCHAR(80) NOT NULL, 
	source_id VARCHAR(64), 
	sample BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (user_id, id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE, 
	CONSTRAINT ck_question_answer CHECK (answer >= 0 AND answer < 6)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_question_course ON questions (user_id, course_id, created_at, id);


CREATE TABLE attempts (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	question_id VARCHAR(64) NOT NULL, 
	submission_id VARCHAR(100) NOT NULL, 
	choice INTEGER NOT NULL, 
	correct BOOL NOT NULL, 
	seconds INTEGER NOT NULL, 
	point VARCHAR(80) NOT NULL, 
	question_snapshot JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id, course_id) REFERENCES courses (user_id, id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id, question_id) REFERENCES questions (user_id, id) ON DELETE CASCADE, 
	CONSTRAINT uq_attempt_submission UNIQUE (user_id, submission_id), 
	CONSTRAINT ck_attempt_values CHECK (choice >= 0 AND choice < 6 AND seconds >= 0 AND seconds <= 86400)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_attempt_owner_course_date ON attempts (user_id, course_id, created_at, id);

CREATE INDEX ix_attempt_owner_date ON attempts (user_id, created_at, id);

CREATE INDEX ix_attempt_owner_question ON attempts (user_id, question_id, created_at, id);


CREATE TABLE knowledge_edges (
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	source VARCHAR(64) NOT NULL, 
	target VARCHAR(64) NOT NULL, 
	PRIMARY KEY (user_id, course_id, source, target), 
	FOREIGN KEY(user_id, course_id, source) REFERENCES knowledge_nodes (user_id, course_id, id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id, course_id, target) REFERENCES knowledge_nodes (user_id, course_id, id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;

CREATE INDEX ix_edge_target ON knowledge_edges (user_id, course_id, target);


CREATE TABLE knowledge_task_links (
	user_id VARCHAR(32) NOT NULL, 
	course_id VARCHAR(64) NOT NULL, 
	node_id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	PRIMARY KEY (user_id, course_id, node_id), 
	FOREIGN KEY(user_id, course_id, node_id) REFERENCES knowledge_nodes (user_id, course_id, id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id, task_id) REFERENCES tasks (user_id, id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_bin

;
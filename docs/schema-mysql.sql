-- Reference only; apply changes through Alembic migrations.

CREATE TABLE app_settings (
	`key` VARCHAR(50) NOT NULL,
	value VARCHAR(200) NOT NULL,
	PRIMARY KEY (`key`)
);

CREATE TABLE audit_logs (
	id INTEGER NOT NULL AUTO_INCREMENT,
	actor VARCHAR(30) NOT NULL,
	module VARCHAR(40) NOT NULL,
	action VARCHAR(80) NOT NULL,
	detail VARCHAR(1000) NOT NULL,
	status VARCHAR(20) NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE INDEX ix_audit_logs_module ON audit_logs (module);

CREATE TABLE roles (
	id VARCHAR(32) NOT NULL,
	name VARCHAR(40) NOT NULL,
	permissions JSON NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE accounts (
	username VARCHAR(30) NOT NULL,
	name VARCHAR(40) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	role_id VARCHAR(32) NOT NULL,
	enabled BOOL NOT NULL,
	email VARCHAR(254) NOT NULL,
	team VARCHAR(80) NOT NULL,
	note VARCHAR(500) NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (username),
	FOREIGN KEY(role_id) REFERENCES roles (id)
);

CREATE TABLE datasets (
	id VARCHAR(32) NOT NULL,
	name VARCHAR(200) NOT NULL,
	filename VARCHAR(200) NOT NULL,
	source VARCHAR(100) NOT NULL,
	is_demo BOOL NOT NULL,
	status VARCHAR(20) NOT NULL,
	row_count BIGINT NOT NULL,
	start_ms BIGINT,
	end_ms BIGINT,
	summary JSON NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	created_by VARCHAR(30) NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(created_by) REFERENCES accounts (username)
);

CREATE INDEX ix_datasets_status ON datasets (status);

CREATE TABLE login_sessions (
	token_hash VARCHAR(64) NOT NULL,
	account_id VARCHAR(30) NOT NULL,
	csrf VARCHAR(64) NOT NULL,
	expires_at BIGINT NOT NULL,
	PRIMARY KEY (token_hash),
	FOREIGN KEY(account_id) REFERENCES accounts (username)
);

CREATE INDEX ix_login_sessions_account_id ON login_sessions (account_id);

CREATE INDEX ix_login_sessions_expires_at ON login_sessions (expires_at);

CREATE TABLE messages (
	id INTEGER NOT NULL AUTO_INCREMENT,
	author_id VARCHAR(30) NOT NULL,
	author_name VARCHAR(40) NOT NULL,
	type VARCHAR(30) NOT NULL,
	content TEXT NOT NULL,
	done BOOL NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(author_id) REFERENCES accounts (username)
);

CREATE TABLE jobs (
	id VARCHAR(32) NOT NULL,
	kind VARCHAR(20) NOT NULL,
	status VARCHAR(20) NOT NULL,
	dataset_id VARCHAR(32) NOT NULL,
	created_by VARCHAR(30) NOT NULL,
	payload JSON NOT NULL,
	processed_rows BIGINT NOT NULL,
	error VARCHAR(1000) NOT NULL,
	created_at BIGINT NOT NULL,
	updated_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(dataset_id) REFERENCES datasets (id),
	FOREIGN KEY(created_by) REFERENCES accounts (username)
);

CREATE INDEX ix_jobs_status_created ON jobs (status, created_at);

CREATE TABLE model_versions (
	id VARCHAR(64) NOT NULL,
	dataset_id VARCHAR(32) NOT NULL,
	name VARCHAR(100) NOT NULL,
	metrics JSON NOT NULL,
	note VARCHAR(500) NOT NULL,
	created_by VARCHAR(30) NOT NULL,
	created_at BIGINT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(dataset_id) REFERENCES datasets (id),
	FOREIGN KEY(created_by) REFERENCES accounts (username)
);

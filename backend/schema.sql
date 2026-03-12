CREATE TABLE role (
	id SERIAL NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE "user" (
	id SERIAL NOT NULL, 
	email VARCHAR NOT NULL, 
	hashed_password VARCHAR NOT NULL, 
	full_name VARCHAR NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	role_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(role_id) REFERENCES role (id)
);

CREATE TABLE project (
	id SERIAL NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR DEFAULT '' NOT NULL,
	is_active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE template (
	id SERIAL NOT NULL, 
	project_id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	file_path VARCHAR NOT NULL, 
	mapping_config VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id)
);

CREATE TABLE routing (
	id SERIAL NOT NULL, 
	project_id INTEGER NOT NULL, 
	destination_type VARCHAR NOT NULL, 
	destination_config VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id)
);

CREATE TABLE integrationsetting (
	id SERIAL NOT NULL, 
	provider_name VARCHAR NOT NULL, 
	config_json VARCHAR NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE meetingsession (
	id SERIAL NOT NULL, 
	fireflies_id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	date VARCHAR NOT NULL, 
	project_id INTEGER, 
	raw_transcript VARCHAR NOT NULL, 
	raw_summary VARCHAR NOT NULL, 
	processed_decisions VARCHAR NOT NULL, 
	processed_risks VARCHAR NOT NULL, 
	processed_agreements VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES project (id)
);

CREATE TABLE actionitem (
	id SERIAL NOT NULL, 
	session_id INTEGER NOT NULL, 
	owner_name VARCHAR NOT NULL, 
	owner_email VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	description VARCHAR NOT NULL, 
	due_date VARCHAR, 
	external_id VARCHAR, 
	is_approved BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES meetingsession (id)
);


INSERT INTO role (name, description, is_active) VALUES ('admin', 'Acceso Total al Sistema', true);
INSERT INTO role (name, description, is_active) VALUES ('validator', 'Validador y Curador de Actas', true);

INSERT INTO "user" (email, hashed_password, full_name, is_active, role_id) 
VALUES ('admin@secretaria.ai', '$2b$12$dqVOzHWGgmskc6KjROQnIeJg/JRRDMsmnWJbNxFRkI8FUb7UHhGEG', 'System Admin', true, (SELECT id FROM role WHERE name='admin'));

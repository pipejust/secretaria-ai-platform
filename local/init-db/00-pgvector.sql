-- Se ejecuta automáticamente la primera vez que arranca el contenedor postgres.
-- pgvector ya viene instalado en pgvector/pgvector:pg17, solo hay que habilitarlo.
CREATE EXTENSION IF NOT EXISTS vector;

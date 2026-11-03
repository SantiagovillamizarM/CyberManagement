-- Se ejecuta UNA sola vez, cuando el volumen de PostgreSQL está vacío.
-- Crea un usuario y una base de datos separados para n8n, de modo que n8n
-- no tenga acceso a las tablas de CyberManagement.
\getenv n8n_db N8N_DB_NAME
\getenv n8n_user N8N_DB_USER
\getenv n8n_password N8N_DB_PASSWORD

CREATE ROLE :"n8n_user" WITH LOGIN PASSWORD :'n8n_password';
CREATE DATABASE :"n8n_db" OWNER :"n8n_user";

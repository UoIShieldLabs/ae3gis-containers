-- Historian database seed for the Industroyer case-study lab.
-- Applied on container start (idempotent). Educational, isolated-lab use.

CREATE DATABASE IF NOT EXISTS telemetry;
USE telemetry;

-- The historian's legitimate job: store OT telemetry polled from the RTU for
-- business reporting. (A few sample rows so a dump shows real-looking data.)
CREATE TABLE IF NOT EXISTS feeder_samples (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  ts         DATETIME DEFAULT CURRENT_TIMESTAMP,
  breaker    VARCHAR(8),
  current_a  DOUBLE,
  voltage_kv DOUBLE
);
INSERT INTO feeder_samples (breaker, current_a, voltage_kv) VALUES
  ('CLOSED', 420, 11.0), ('CLOSED', 419, 11.0), ('CLOSED', 421, 11.0);

-- LAB-ONLY / DELIBERATELY INSECURE (secret sprawl): the historian stores OS and OT
-- credentials in a world-queryable table. An intruder who can reach the DB learns
-- the historian's own MAINTENANCE login (distinct credential) and a pointer to the
-- OT engineering SSH key - exactly what they need to pivot deeper. Never do this.
CREATE TABLE IF NOT EXISTS ot_credentials (
  service   VARCHAR(32),
  host      VARCHAR(24),
  username  VARCHAR(32),
  secret    VARCHAR(96),
  note      VARCHAR(160)
);
DELETE FROM ot_credentials;
INSERT INTO ot_credentials (service, host, username, secret, note) VALUES
  ('historian-maint', '10.0.4.30', 'histadmin', 'H1st0r!an',
   'SSH maintenance login for the historian host'),
  ('ot-engineering',  '10.0.3.15', 'root', 'key:/etc/ot-collector/ot_engineering_key',
   'Engineering workstation trusts this OT SSH key; private key stored on this host'),
  ('rtu-fieldbus',    '10.0.3.50', '-', '-',
   'IEC-104 field device on tcp/2404 - no authentication');

-- The DMZ gateway's reporting account (used over the sanctioned report push); its
-- credentials sit in /etc/filebroker/report.cnf on the gateway - the foothold for
-- the DB extraction.
CREATE USER IF NOT EXISTS 'report'@'%' IDENTIFIED BY 'r3p0rt';
GRANT SELECT, INSERT ON telemetry.* TO 'report'@'%';
FLUSH PRIVILEGES;

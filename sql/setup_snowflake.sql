-- =============================================================================
-- DataMind Receipts — Snowflake Setup Script
-- =============================================================================
-- Run ONCE as ACCOUNTADMIN (the default role on a Snowflake trial account).
--
-- Creates:
--   DM_WH         XSMALL warehouse, 60 s auto-suspend, 30 s statement timeout
--   DM_RM         Resource monitor, 5-credit quota
--   DATAMIND       Database
--   DATAMIND.OWID  Schema for OWID indicator tables
--   DM_LOADER      Role that owns and can mutate tables (human operator only)
--   DM_READER      Role for the app — SELECT only
--
-- After running, replace <APP_SERVICE_USER> and <OPERATOR_USER> at the bottom
-- with your actual Snowflake user names.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- ── Warehouse ────────────────────────────────────────────────────────────────
CREATE WAREHOUSE IF NOT EXISTS DM_WH
  WAREHOUSE_SIZE   = 'XSMALL'
  AUTO_SUSPEND     = 60
  AUTO_RESUME      = TRUE
  INITIALLY_SUSPENDED = TRUE
  STATEMENT_TIMEOUT_IN_SECONDS = 30;

-- ── Resource monitor (5-credit safety net) ───────────────────────────────────
CREATE RESOURCE MONITOR IF NOT EXISTS DM_RM
  WITH CREDIT_QUOTA = 5
  TRIGGERS
    ON 80 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND;

ALTER WAREHOUSE DM_WH SET RESOURCE_MONITOR = DM_RM;

-- ── Database and schema ──────────────────────────────────────────────────────
CREATE DATABASE IF NOT EXISTS DATAMIND;
CREATE SCHEMA IF NOT EXISTS DATAMIND.OWID;

-- ── DM_LOADER role (human operator only — never used by the web app) ─────────
-- Owns the tables it creates, so it can also UPDATE them for the demo mutation.
CREATE ROLE IF NOT EXISTS DM_LOADER;
GRANT USAGE ON WAREHOUSE DM_WH                TO ROLE DM_LOADER;
GRANT USAGE ON DATABASE  DATAMIND              TO ROLE DM_LOADER;
GRANT USAGE,
      CREATE TABLE,
      CREATE FILE FORMAT,
      CREATE STAGE
  ON SCHEMA DATAMIND.OWID                      TO ROLE DM_LOADER;

-- ── DM_READER role (app / service user — SELECT only) ────────────────────────
CREATE ROLE IF NOT EXISTS DM_READER;
GRANT USAGE  ON WAREHOUSE DM_WH               TO ROLE DM_READER;
GRANT USAGE  ON DATABASE  DATAMIND             TO ROLE DM_READER;
GRANT USAGE  ON SCHEMA    DATAMIND.OWID        TO ROLE DM_READER;
GRANT SELECT ON ALL TABLES    IN SCHEMA DATAMIND.OWID TO ROLE DM_READER;
GRANT SELECT ON FUTURE TABLES IN SCHEMA DATAMIND.OWID TO ROLE DM_READER;

-- Allow reading TPC-H sample data (config-only second dataset)
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE_SAMPLE_DATA TO ROLE DM_READER;

-- ── Assign roles to users ────────────────────────────────────────────────────
-- Replace the placeholders below with your actual Snowflake usernames.
-- Prefer SEPARATE principals: the app/service user gets ONLY DM_READER;
-- a human/operator account gets DM_LOADER for setup and demo scripts.
-- Do NOT grant DM_LOADER to the app/service user.
GRANT ROLE DM_READER TO USER <APP_SERVICE_USER>;
GRANT ROLE DM_LOADER TO USER <OPERATOR_USER>;

-- The Snowflake connector sets QUERY_TAG = 'datamind' on every app session.

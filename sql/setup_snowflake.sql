-- ============================================================================
-- DataMind Receipts — Snowflake Warehouse & RBAC Setup Script
-- Run as ACCOUNTADMIN or SYSADMIN in Snowflake worksheet or SnowSQL
-- Handbook Section 7 Specification
-- ============================================================================

USE ROLE ACCOUNTADMIN;

-- 1. Create Data Warehouse (standard extra small for low compute credit usage)
CREATE WAREHOUSE IF NOT EXISTS DM_WH
  WITH WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'DataMind query engine warehouse';

-- 2. Create Target Database & Schema for OWID Indicators
CREATE DATABASE IF NOT EXISTS DATAMIND
  COMMENT = 'DataMind ground-truth indicators database';

CREATE SCHEMA IF NOT EXISTS DATAMIND.OWID
  COMMENT = 'Our World in Data health, economic, and demographic indicators';

-- 3. Define OWID Tables
CREATE TABLE IF NOT EXISTS DATAMIND.OWID.life_expectancy (
  entity VARCHAR(150) NOT NULL,
  code VARCHAR(10),
  year INTEGER NOT NULL,
  life_expectancy FLOAT NOT NULL,
  PRIMARY KEY (entity, year)
);

CREATE TABLE IF NOT EXISTS DATAMIND.OWID.gdp_per_capita (
  entity VARCHAR(150) NOT NULL,
  code VARCHAR(10),
  year INTEGER NOT NULL,
  gdp_per_capita FLOAT NOT NULL,
  PRIMARY KEY (entity, year)
);

CREATE TABLE IF NOT EXISTS DATAMIND.OWID.co2 (
  entity VARCHAR(150) NOT NULL,
  code VARCHAR(10),
  year INTEGER NOT NULL,
  co2 FLOAT NOT NULL,
  PRIMARY KEY (entity, year)
);

CREATE TABLE IF NOT EXISTS DATAMIND.OWID.population (
  entity VARCHAR(150) NOT NULL,
  code VARCHAR(10),
  year INTEGER NOT NULL,
  population BIGINT NOT NULL,
  PRIMARY KEY (entity, year)
);

-- ============================================================================
-- 4. RBAC Roles Setup
-- DM_READER: Read-only role used exclusively by the FastAPI application
-- DM_LOADER: Operator role used only for loading OWID datasets & live demo mutations
-- ============================================================================

CREATE ROLE IF NOT EXISTS DM_READER
  COMMENT = 'Read-only application role for DataMind API queries (SELECT only)';

CREATE ROLE IF NOT EXISTS DM_LOADER
  COMMENT = 'Data loading and operator role (used by Person A only)';

-- Warehouse permissions
GRANT USAGE ON WAREHOUSE DM_WH TO ROLE DM_READER;
GRANT USAGE ON WAREHOUSE DM_WH TO ROLE DM_LOADER;

-- Database & Schema USAGE permissions
GRANT USAGE ON DATABASE DATAMIND TO ROLE DM_READER;
GRANT USAGE ON DATABASE DATAMIND TO ROLE DM_LOADER;
GRANT ALL ON SCHEMA DATAMIND.OWID TO ROLE DM_LOADER;

GRANT USAGE ON SCHEMA DATAMIND.OWID TO ROLE DM_READER;
GRANT SELECT ON ALL TABLES IN SCHEMA DATAMIND.OWID TO ROLE DM_READER;
GRANT SELECT ON FUTURE TABLES IN SCHEMA DATAMIND.OWID TO ROLE DM_READER;

-- TPC-H Sample Data read permissions for DM_READER
GRANT USAGE ON DATABASE SNOWFLAKE_SAMPLE_DATA TO ROLE DM_READER;
GRANT USAGE ON SCHEMA SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 TO ROLE DM_READER;
GRANT SELECT ON ALL TABLES IN SCHEMA SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 TO ROLE DM_READER;

-- Assign DM_READER to CURRENT_USER for testing
SET my_user = CURRENT_USER();
GRANT ROLE DM_READER TO USER IDENTIFIER($my_user);
GRANT ROLE DM_LOADER TO USER IDENTIFIER($my_user);

SELECT 'DataMind Snowflake Setup Completed Successfully' AS status;

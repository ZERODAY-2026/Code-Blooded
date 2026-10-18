-- =============================================================================
-- sql/load_owid.sql — OWID Table Creation and Zero-Drift Backup Clone
-- =============================================================================
-- Run strictly as DM_LOADER.
-- Used to initialize the table structure before loading CSV data,
-- and creates the backup clone used for demonstration resets.
-- =============================================================================

USE ROLE DM_LOADER;
USE WAREHOUSE DM_WH;
USE DATABASE DATAMIND;
USE SCHEMA OWID;

-- ── 1. Create base table matching datasets/owid.yaml ─────────────────────────
CREATE OR REPLACE TABLE life_expectancy (
    entity           VARCHAR(256)  NOT NULL,
    code             VARCHAR(10),
    year             INTEGER       NOT NULL,
    life_expectancy  FLOAT
);

-- ── 2. Create zero-drift backup clone ────────────────────────────────────────
-- Used by demo reset scripts. Backup tables are omitted from YAML allowlists,
-- so the application can never query or expose them.
CREATE OR REPLACE TABLE life_expectancy_backup CLONE life_expectancy;

-- =============================================================================
-- sql/admin_update_demo.sql — Local Operator Data-Change Demo
-- =============================================================================
-- ZERO-TOLERANCE RULES (AGENTS.md):
--   1. The data-change demo NEVER uses the LLM path.
--   2. Run only by an operator locally using DM_LOADER credentials.
--   3. DM_LOADER credentials are NEVER exposed to FastAPI or the deployed web app.
--   4. Never run mutations on SNOWFLAKE_SAMPLE_DATA (read-only shared data).
--      Mutations run exclusively on our own table: DATAMIND.OWID.life_expectancy.
-- =============================================================================

USE ROLE DM_LOADER;
USE WAREHOUSE DM_WH;
USE DATABASE DATAMIND;
USE SCHEMA OWID;

-- ── 1. Mutation: Change Japan's 2019 life expectancy to 95.0 ─────────────────
-- This noticeably changes the top-5 ranking for 2019.
UPDATE life_expectancy
SET life_expectancy = 95.0
WHERE entity = 'Japan' AND year = 2019;

-- Check the change
SELECT entity, year, life_expectancy
FROM life_expectancy
WHERE entity = 'Japan' AND year = 2019;

-- ── 2. Time Travel: Query the state BEFORE the mutation ─────────────────────
-- Shows the original value at an earlier statement/timestamp without restoring.
-- SELECT entity, year, life_expectancy
-- FROM life_expectancy BEFORE(STATEMENT => '<UPDATE_QUERY_ID>')
-- WHERE entity = 'Japan' AND year = 2019;

-- ── 3. Reset: Restore original value from zero-drift backup clone ─────────────
-- UPDATE life_expectancy t
-- SET life_expectancy = b.life_expectancy
-- FROM life_expectancy_backup b
-- WHERE t.entity = b.entity
--   AND t.year = b.year
--   AND t.entity = 'Japan'
--   AND t.year = 2019;

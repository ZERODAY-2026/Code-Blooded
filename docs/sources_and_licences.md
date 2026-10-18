# Datasets, Sources, and Licences

> **DataMind Receipts — Problem Statement 1 (PS1)**  
> **Compliance with Handbook Gate G4 (Data Disclosure & Licensing)**

---

## 1. Our World in Data (OWID)

### Overview
Our World in Data (OWID) indicators provide historical socio-economic, demographic, and public health metrics. These tables are hosted within our dedicated Snowflake database (`DATAMIND.OWID`).

### Table Inventory & Indicator Attribution
| Table Name | Description | Upstream Source & Attribution | Licence |
| :--- | :--- | :--- | :--- |
| `DATAMIND.OWID.life_expectancy` | Life expectancy at birth across nations and years | Our World in Data — [TODO: Upstream download URL & dataset commit SHA from Person A] | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `DATAMIND.OWID.gdp_per_capita` | GDP per capita in constant international dollars (planned) | Our World in Data / World Bank | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `DATAMIND.OWID.co2` | Annual CO2 emissions per capita (planned) | Our World in Data / Global Carbon Budget | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `DATAMIND.OWID.population` | National total population figures (planned) | Our World in Data / UN Population Division | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

### Data Preparation & Ingestion Steps
1. **Raw Ingestion**: Data fetched via ingestion scripts using clean ISO 3-letter country codes (`code`).
2. **Entity Filtering**: Regional aggregates and sub-national groups are filtered in queries using `LEFT(code, 5) <> 'OWID_'` to avoid double-counting national figures.
3. **Database Isolation**: Loaded into `DATAMIND.OWID` with read-only permissions granted to `DM_READER`. Operators perform mutations or updates exclusively under the separate `DM_LOADER` role.

---

## 2. TPC-H Benchmark (SF1)

### Overview
The Transaction Processing Performance Council (TPC) TPC-H benchmark represents a standard decision support workload modeling wholesale suppliers, parts, orders, and customer line items.

### Tables Referenced
The following 8 tables are referenced live at Scale Factor 1 (~1 GB database):
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.CUSTOMER` (150,000 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS` (1,500,000 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.LINEITEM` (6,001,215 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.SUPPLIER` (10,000 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.PARTSUPP` (800,000 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.PART` (200,000 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.NATION` (25 rows)
- `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.REGION` (5 rows)

### Non-Redistribution & Gate G4 Compliance
- **Zero Local Redistribution**: In compliance with handbook Gate G4, **no TPC-H data files, parquet dumps, or CSVs are committed to this repository**.
- **Live Cloud Reference**: All TPC-H queries execute directly against the shared data share provisioned natively by Snowflake (`SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`).
- **Access Role**: `DM_READER` is granted `USAGE` on the database and schema, and `SELECT` on all tables in `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`.

---

## 3. Project Licence

The DataMind codebase and documentation are released under the **MIT License** — see [LICENSE](../LICENSE) for complete terms.

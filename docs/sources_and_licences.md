# Data Sources and Licences

This document discloses all datasets used by DataMind Receipts, their licences,
and how they are accessed. This satisfies hackathon gate **G4** (data disclosure).

---

## 1. Our World in Data (OWID) — Life Expectancy

| Field         | Details |
| :------------ | :------ |
| **Dataset**   | Life Expectancy at Birth |
| **Source**     | [Our World in Data — Life Expectancy](https://ourworldindata.org/grapher/life-expectancy) |
| **Upstream**   | United Nations World Population Prospects and other sources compiled by OWID |
| **Licence**   | [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/) |
| **How loaded** | `scripts/download_owid.py` downloads the public CSV at runtime and loads it into `DATAMIND.OWID.LIFE_EXPECTANCY` using the `DM_LOADER` role. The raw CSV is **not committed** to the repository. |
| **Columns**   | `entity` (country/region name), `code` (ISO 3166-1 alpha-3), `year`, `life_expectancy` (years) |
| **Attribution** | Data from Our World in Data, licensed under CC BY 4.0. When citing, credit: *"Our World in Data — Life Expectancy."* |

> **Note:** If additional OWID indicators (GDP per capita, CO2, population) are
> added later, each must be checked for its specific licence (most are CC BY 4.0)
> and attributed individually in this document.

---

## 2. TPC-H Sample Data (Snowflake)

| Field         | Details |
| :------------ | :------ |
| **Dataset**   | TPC-H Benchmark Data |
| **Source**     | `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` — pre-loaded in every Snowflake account |
| **Licence**   | Available under the [TPC EULA](http://www.tpc.org/tpc_documents_current_versions/pdf/tpc_eula.pdf). Data is **referenced in-account only** and never copied or committed to this repository. |
| **How accessed** | The `DM_READER` role is granted `IMPORTED PRIVILEGES` on `SNOWFLAKE_SAMPLE_DATA`. The application queries it live when `config.yaml` points `dataset:` to `datasets/tpch.yaml`. |
| **Attribution** | TPC Benchmark™ H — Standard Specification. Data is provided by Snowflake as a shared sample database. |

> **Important:** TPC-H and TPC-DS sample data are **reference only**. Do not copy
> any TPC data into the repository. The Snowflake shared database is read-only;
> never attempt to insert into `SNOWFLAKE_SAMPLE_DATA`.

---

## Summary of Commitments

- **No non-redistributable data is committed** to this repository.
- Raw CSVs are downloaded at runtime by the load scripts; they are not checked in.
- `SNOWFLAKE_SAMPLE_DATA` is accessed in-account only — never exported or stored locally.
- Each dataset's licence is disclosed above with a link to the full licence text.
- Additional datasets added in the future must be documented here before merging.

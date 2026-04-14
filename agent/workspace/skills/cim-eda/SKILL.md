---
name: cim-eda
description: CIM EDA（Engineering Data Analysis）與 CIM 核心資料查詢。當使用者詢問 EDA、EDWM、良率分析、根因分析、correlation、Traceability、wafer summary、region summary、shot summary、site raw data、chip raw data、original wafer id、notch down、WAT/CP/FT/LQC/MQC/FDC/Defect 跨站點整合分析、lot tracking、WIP、製程流程、cimdb、AI 分析資料、設備製程參數、recipe、chamber 資料時觸發。負責的 catalog：edadb（設備/recipe/chamber 資料）、cimdb（EDA/EDWM 整合分析與 CIM 核心）、ccai（CIM AI）。透過 Confluence MCP 查詢表格文件，再以 Trino MCP 執行查詢。若查詢涉及 cimdb.edwmuser metadata view，優先使用 edwm-query skill 的 discovery 方式。
---

# CIM Engineering Data Analysis / CIM Core Query

**Catalogs**: `edadb`（設備/recipe/chamber）、`cimdb`（EDA/EDWM 分析與 CIM 核心）、`ccai`（AI 平台）

## 系統定位

EDA = Engineering Data Analysis。核心目標是整合各站點量測與製程資料，支援良率提升、根因分析、品質追溯與生產效率改善。

- 常見主題：WAT、CP、FT、LQC、MQC、FDC、Defect、Traceability
- 常見資料層級：wafer summary、region summary、shot summary、site raw data、chip raw data
- 常見情境：把晶圓從 FAB 第一站到最後一站（CP/FT）的量測與歷程資料串起來
- 常見關聯 key：`original_wafer_id`
- 座標資料預設以 notch down 對齊

## Workflow

### 1. 判斷子系統
- EDA / EDWM、良率分析、根因分析、跨站點 WAT/CP/FT/LQC/MQC/FDC/Defect 關聯、Traceability、summary/raw data → 優先 `cimdb`
- 設備資料、recipe、chamber、機台參數 → `edadb`
- lot tracking、WIP、製程流程、製程管理 → `cimdb`
- AI 分析、預測模型輸出 → `ccai`

### 2. cimdb / EDWM 特殊處理

EDA / EDWM 類問題優先從 `cimdb` 找候選表；若是整合分析題，優先留意 `original_wafer_id`、資料層級（summary / raw）與時間欄位定義。

`cimdb` 有 `edwmuser` schema 提供 metadata view，優先用以下方式做 discovery：

```sql
-- 找候選表（edwm metadata view）
SELECT * FROM cimdb.edwmuser.edwm_table_list_v
WHERE table_comment LIKE '%<關鍵詞>%'
   OR table_name LIKE '%<關鍵詞>%';

-- 找欄位
SELECT * FROM cimdb.edwmuser.edwm_column_list_v
WHERE table_name = '<table>' ;
```

### 3. Table Discovery（一般方式）

**Step A：Confluence MCP（優先）**

搜尋 Confluence space `A0IMKB`，關鍵詞為業務描述或表名。
取得：業務描述、欄位說明、join key、範例 SQL。

**Step B：Fallback（Confluence 無此表）**

```sql
-- 列出所有表
SELECT table_schema, table_name
FROM <catalog>.information_schema.tables
WHERE table_schema NOT IN ('information_schema')
ORDER BY table_schema, table_name;

-- 查欄位
SELECT column_name, data_type, comment
FROM <catalog>.information_schema.columns
WHERE table_schema = '<schema>' AND table_name = '<table>';

-- 樣本
SELECT * FROM <catalog>.<schema>.<table> LIMIT 5;
```

### 4. 產出 SQL

- 全名格式：`edadb.<schema>.<table>` 或 `cimdb.<schema>.<table>`
- 時間條件：`TIMESTAMP 'YYYY-MM-DD HH:MM:SS'`
- 跨站點整合分析優先確認 join key，常見為 `original_wafer_id`
- 時間欄位依資料域選用，不要假設所有表都用 `met_dt`
- summary 表通常已預先整理統計值；座標類分析預設為 notch down

### 5. 回應格式

```
### SQL
[SQL]

### 表來源
- 主表：[catalog.schema.table]
- 選表理由：

### Join Key / 假設

### 結果解讀
- 若要追 raw data，下一張表：
```

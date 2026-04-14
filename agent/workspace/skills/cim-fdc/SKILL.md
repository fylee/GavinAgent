---
name: cim-fdc
description: CIM FDC（Fault Detection and Classification）系統資料查詢。當使用者詢問 FDC、即時製程控制、故障偵測、bad run、abnormal run、UVA、MVA、Raw Data、Time Data、Run Summary、Lot Summary、Lot Run、Process Alarm、Equipment Alarm、Spec 卡控、製程參數趨勢、設備異常、run data、lot run 相關資料時觸發。負責的 catalog：ctfdc（台中廠）、khfdc（高雄廠）。透過 Confluence MCP 查詢表格文件，再以 Trino MCP 執行查詢。
---

# CIM FDC Query

**Catalogs**: `ctfdc`（台中）、`khfdc`（高雄）

## 系統定位

FDC = Fault Detection and Classification。核心目標是從設備收集海量 process data，進行即時製程控制與故障偵測，降低良率損失、改善 cycle time，並提升設備稼動率。

- 常見設備域：CMP、ETCH、LITHO、WET、DIFF、TF、IMP、CMS、CP、Facility
- 常見資料型態：Lot Run、Raw Data（Time Data）、Run Summary、Lot Summary、UVA
- 常見控制邏輯：Raw / UVA / MVA Spec 卡控、Process Alarm、Equipment Alarm
- 常見分類維度：Equipment、Chamber、Recipe、SVID、Data Type、Run

## Workflow

### 1. 判斷廠別
- `ct` / `台中` → `ctfdc`
- `kh` / `高雄` → `khfdc`
- 未指定 → 優先問使用者，或兩廠都查

### 2. 判斷查詢類型

- 即時異常、Spec 卡控、alarm 追查 → Process Alarm / Equipment Alarm 類表
- 趨勢、統計、有哪些異常 lot / run → summary / lot 層級表
- 單次 run 明細、秒級參數、raw trace → raw / time / run 類表
- UVA / MVA 超限或模型結果 → UVA / model 類表

### 3. Table Discovery

**Step A：Confluence MCP（優先）**

搜尋 Confluence space `A0IMKB`，關鍵詞為業務描述（如 bad run、UVA、MVA、Raw Data、Lot Run、Process Alarm、Equipment Alarm）或表名。
取得：業務描述、欄位說明、join key、範例 SQL。

**Step B：Fallback（Confluence 無此表）**

```sql
-- 列出 catalog 下所有 schema 與表
SELECT table_schema, table_name
FROM <catalog>.information_schema.tables
WHERE table_schema NOT IN ('information_schema')
ORDER BY table_schema, table_name;

-- 查欄位定義
SELECT column_name, data_type, comment
FROM <catalog>.information_schema.columns
WHERE table_schema = '<schema>' AND table_name = '<table>';

-- 看樣本資料（必要時）
SELECT * FROM <catalog>.<schema>.<table> LIMIT 5;
```

### 4. 判斷是否 summary 優先

- 使用者要趨勢、統計、有哪些 → 找 summary / lot 層級表
- 使用者要明細、單點確認 → 找 raw / run 層級表

### 5. 產出 SQL

- 全名格式：`ctfdc.<schema>.<table>` 或 `khfdc.<schema>.<table>`
- 時間條件：`TIMESTAMP 'YYYY-MM-DD HH:MM:SS'`
- 探索階段先加 `LIMIT 100`
- JOIN 時優先用 `eqp_id + run_id`（FDC 常見複合 key）
- 若是 run 內參數分析，留意同一 run 下的 time sequence 與 SVID / data type 維度
- UVA 常見由 raw data 聚合而來，確認模型、統計方式（如 mean / sigma / max / min）與 alarm 邏輯

### 6. 回應格式

```
### SQL
[SQL]

### 表來源
- 主表：[catalog.schema.table]
- 選表理由：

### Join Key / 假設
- join key：
- 時間條件：

### 結果解讀
```

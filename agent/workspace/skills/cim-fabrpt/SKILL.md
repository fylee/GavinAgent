---
name: cim-fabrpt
description: CIM REPORT 系統資料查詢。當使用者詢問 REPORT、FAB 生產資料、WIP、Cycle Time、Yield、OEE、Capacity、wafer out、Lot/WIP/MVMT/Route/Step/Hold、設備狀態、Down Time、產能規劃、生產瓶頸、歷史追溯、製程統計、品質與工程分析相關資料時觸發。負責的 catalog：ctfabrpt（台中廠）、khfabrpt（高雄廠）。透過 Confluence MCP 查詢表格文件，再以 Trino MCP 執行查詢。
---

# CIM REPORT System Query

**Catalogs**: `ctfabrpt`（台中）、`khfabrpt`（高雄）

## 系統定位

REPORT 系統用來彙整製造生產資訊，整合製程、品質與測試數據，提供製造、製程、設備、品質人員即時掌握生產狀況，並保留歷史追溯能力，作為生產管理、品質改善、產能規劃與工程決策的共同數據基準。

- 常見主題：WIP、Cycle Time、Yield、OEE、Capacity、wafer out、瓶頸、歷史追溯
- 常見來源：MES、Equipment、FDC / EAP、EDC / Engineering DB、RTS / FOUP、Target / Control Table、Scrap、Defect
- 常見流程資料：Lot、WIP、MVMT、Route、Step、Hold
- 常見場景：流程定義、即時生產狀態、設備資源、品質與工程分析、控制目標查詢

## Workflow

### 1. 判斷廠別
- `ct` / `台中` → `ctfabrpt`
- `kh` / `高雄` → `khfabrpt`
- 未指定 → 優先問使用者，或兩廠都查

### 2. 判斷粒度
- 趨勢、彙整、月報、週報 → 找 summary / 統計表
- 單一 lot / wafer 明細 → 找 detail / raw 表

### 3. 判斷查詢類型
- 在製品、生產進度、wafer out、MVMT、Hold、Route、Step → 製造流程 / WIP 類表
- Cycle Time、Yield、OEE、Capacity、瓶頸 → 指標 / 統計類表
- 設備狀態、Down Time、資源利用 → 設備 / 資源類表
- 歷史追溯、品質異常、工程分析 → event / history / quality 類表

### 4. Table Discovery

**Step A：Confluence MCP（優先）**

搜尋 Confluence space `A0IMKB`，關鍵詞為業務描述（如 WIP、Cycle Time、OEE、Capacity、wafer out、Route、Step、Hold、Down Time）或表名。
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

### 5. 產出 SQL

- 全名格式：`ctfabrpt.<schema>.<table>` 或 `khfabrpt.<schema>.<table>`
- 時間條件：`TIMESTAMP 'YYYY-MM-DD HH:MM:SS'`
- 探索階段先加 `LIMIT 100`
- 前綴查詢用 `LIKE 'ABC%'`
- 歷史追溯題優先確認 lot / equipment / event 的時間序與關聯 key

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

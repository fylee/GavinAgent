---
name: cim-router
description: CIM 資料庫查詢路由器。當使用者詢問任何製造資料、製程資料、設備資料、報表查詢、或 CIM 相關資料但未指定系統時觸發。負責判斷問題屬於哪個 catalog，再路由到對應 Domain Skill 處理。涵蓋 27 個 Trino catalog：cassandra, ccai, cimdb, cp6rpt, cprtc, cpwarpt, ctfabrpt, ctfdc, ctfmcsalarm, ctfmcsrpt, ctoffspc, ctpbi, edadb, es_infra_log, hadoop, imxrpt, jmx, kh_es_infra_log, khalarm, khfabrpt, khfdc, khfmcsrpt, khoffspc, qmsdb, system, udpdb, vscdb。
---

# CIM Router

## 職責

判斷使用者問題屬於哪個系統，套用對應 Domain Skill 的 workflow。不直接執行查詢。

## Step 1：判斷廠別

- 有 `ct` / `台中` → 優先 `ct*` catalog
- 有 `kh` / `高雄` → 優先 `kh*` catalog
- 未指定廠別 → 兩廠都納入，或先問使用者

## Step 2：判斷業務領域

參考 `references/catalogs.md` 的關鍵詞對應表快速判斷。

若關鍵詞不明確，用 Confluence MCP 讀取 CIM 系統總覽頁（Page ID: `444432481`，Space: `A0IMKB`）取得各系統詳細描述後再判斷。

## Step 3：路由到 Domain Skill

| Domain     | Skill        | Catalogs                                                      |
| ---------- | ------------ | ------------------------------------------------------------- |
| FDC        | cim-fdc      | ctfdc, khfdc                                                  |
| FAB Report | cim-fabrpt   | ctfabrpt, khfabrpt                                            |
| MCS/Alarm  | cim-mcs      | ctfmcsalarm, ctfmcsrpt, khfmcsrpt, khalarm                    |
| CP Report  | cim-cprpt    | cp6rpt, cprtc, cpwarpt                                        |
| SPC        | cim-spc      | ctoffspc, khoffspc                                            |
| EDA/EDWM   | cim-eda      | edadb, cimdb, ccai                                            |
| Business   | cim-business | qmsdb, udpdb, vscdb, imxrpt, ctpbi                            |
| Infra      | cim-infra    | cassandra, es_infra_log, kh_es_infra_log, hadoop, jmx, system |

確定 domain 後，直接套用對應 skill 的 workflow 繼續處理。

## References

- `references/catalogs.md`：27 catalog 詳細描述與關鍵詞對應

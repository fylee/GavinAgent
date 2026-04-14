# CIM Catalog 對應表

## 關鍵詞 → Domain Skill

| 關鍵詞                                                                                                                                                                 | Domain Skill |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| FDC、fault、異常偵測、bad run、UVA、MVA、Raw Data、Time Data、Lot Run、Run Summary、Lot Summary、Process Alarm、Equipment Alarm、Spec 卡控、即時製程控制、製程參數監控 | cim-fdc      |
| REPORT、FAB、生產報表、WIP、Cycle Time、Yield、OEE、Capacity、wafer out、MVMT、Route、Step、Hold、Down Time、歷史追溯、瓶頸、產能規劃                                  | cim-fabrpt   |
| MCS、alarm、警報、物料控制、carrier、搬送                                                                                                                              | cim-mcs      |
| CP、chip probing、探針測試、bin、probe yield、wafer acceptance、RTC、測試廠                                                                                            | cim-cprpt    |
| SPC、管制圖、Cpk、製程能力、OffSPC                                                                                                                                     | cim-spc      |
| EDA、EDWM、良率分析、根因分析、Traceability、wafer summary、region summary、shot summary、original wafer id、notch down、跨站點分析、設備資料、cimdb                   | cim-eda      |
| QMS、品質管理、UDP、VSC、IMX、PBI                                                                                                                                      | cim-business |
| log、infra、Cassandra、Hadoop、JMX、監控、ES                                                                                                                           | cim-infra    |

## Catalog 詳細對應

| Catalog         | 業務描述                                                                          | Domain Skill |
| --------------- | --------------------------------------------------------------------------------- | ------------ |
| ctfdc           | 台中廠 FDC 系統（即時製程控制、故障偵測、Raw/UVA/MVA Spec 卡控、Alarm 分析）      | cim-fdc      |
| khfdc           | 高雄廠 FDC 系統（即時製程控制、故障偵測、Raw/UVA/MVA Spec 卡控、Alarm 分析）      | cim-fdc      |
| ctfabrpt        | 台中廠 REPORT 系統（生產、WIP、Cycle Time、Yield、OEE、Capacity、歷史追溯）       | cim-fabrpt   |
| khfabrpt        | 高雄廠 REPORT 系統（生產、WIP、Cycle Time、Yield、OEE、Capacity、歷史追溯）       | cim-fabrpt   |
| ctfmcsalarm     | 台中廠 MCS Alarm（物料控制系統警報記錄）                                          | cim-mcs      |
| ctfmcsrpt       | 台中廠 MCS 報表（搬送、carrier、設備狀態）                                        | cim-mcs      |
| khfmcsrpt       | 高雄廠 MCS 報表                                                                   | cim-mcs      |
| khalarm         | 高雄廠 Alarm 資料                                                                 | cim-mcs      |
| cp6rpt          | 測試廠 CP 測試報表（Chip Probing 第六代平台，bin/yield）                          | cim-cprpt    |
| cprtc           | 測試廠 CP RTC（Real-Time Control）即時控制資料                                    | cim-cprpt    |
| cpwarpt         | 測試廠 CP WA（Wafer Acceptance）報表                                              | cim-cprpt    |
| ctoffspc        | 台中廠 Off-line SPC（統計製程管制，管制圖/Cpk）                                   | cim-spc      |
| khoffspc        | 高雄廠 Off-line SPC                                                               | cim-spc      |
| edadb           | EDA 相關設備/recipe/chamber/機台資料                                              | cim-eda      |
| cimdb           | EDA/EDWM 整合分析資料與 CIM 核心資料（良率分析、Traceability、lot tracking、WIP） | cim-eda      |
| ccai            | EDA / CIM AI 分析平台資料                                                         | cim-eda      |
| qmsdb           | QMS（Quality Management System）品質管理系統                                      | cim-business |
| udpdb           | UDP 資料庫                                                                        | cim-business |
| vscdb           | VSC 資料庫                                                                        | cim-business |
| imxrpt          | IMX 報表                                                                          | cim-business |
| ctpbi           | CT Power BI 資料來源                                                              | cim-business |
| cassandra       | Cassandra NoSQL，存放時序/監控資料                                                | cim-infra    |
| es_infra_log    | Elasticsearch Infrastructure Log（台中）                                          | cim-infra    |
| kh_es_infra_log | Elasticsearch Infrastructure Log（高雄）                                          | cim-infra    |
| hadoop          | Hadoop 大數據平台資料                                                             | cim-infra    |
| jmx             | JMX（Java Management Extensions）系統監控指標                                     | cim-infra    |
| system          | Trino 系統 metadata                                                               | cim-infra    |

<!-- doc_id: doc_sanguo_ragops_0001 -->
# 三國 RAG 證據資料產線 PostgreSQL 與向量化開發計畫

## 目的

正式推進三國資料管線時，外部採證、anchor 驗證、seed scoring、evidence card、scoreboard snapshot 與 proposal ledger 會產生大量 JSON/JSONL 證據。這些資料若長期只靠散落檔案運作，會遇到檔案數爆量、resume 掃描變慢、重跑去重困難、向量回滾不可控、以及治理證據難以查詢的問題。

本計畫目標是建立一條可大量試跑的資料產線：保留 JSONL 可回放審計能力，同時把高量證據索引到 PostgreSQL，並只把 retrieval-ready chunks 寫入 Qdrant/Pinecone 等向量資料庫。

## 設計結論

目前 repo 已有 PostgreSQL、Qdrant/Pinecone 與 vector ingestion gate 的基礎，但還不是完整 evidence production backend：

- PostgreSQL 現有 schema 主要覆蓋 observed mentions、alias map、triage decisions，尚未覆蓋 harvested pages、evidence seeds、evidence cards、anchor passages、run/source ledger。
- Vector exporter 目前主要輸出 events、keywords、persona records，尚未把 anchor passages 與 evidence cards 納入正式 retrieval-ready record schema。
- 現有 policy 已要求 PostgreSQL 先走 evaluate/plan/adapter/dual-write，再 cutover；vector production 也必須先 smoke namespace、dedupe/resume/probe，再 promotion。

因此不應把所有 JSON 直接塞入雲端向量庫，也不應立刻切 PostgreSQL-only。正確架構是三層分工：

1. Artifact lake：保留原始 JSONL/頁面/telemetry，採分區與壓縮，作為可回放審計層。
2. PostgreSQL：保存可查詢治理狀態、provenance、dedupe key、run/source/page/seed/card/anchor/proposal ledger。
3. Vector DB：只保存語意檢索需要的 anchor passages、accepted/candidate evidence cards、events、persona、keywords 等 retrieval-ready chunks。

## 架構紅線

- `canonicalWrites=false` 的治理語意不可被資料庫導入改變；切換前 JSONL 仍是 canonical export mirror。
- PostgreSQL 初期只能作 mirror/backfill/readiness，不得直接成為唯一 source of truth。
- Vector DB 不保存全部 raw seed，也不保存無清理、無 provenance 的頁面雜訊。
- anchor locator/textHash 只能作 supporting provenance，不得偽裝成外部網站自身 locator。
- 所有 budget、threshold、namespace、source policy 必須資料化或讀 policy，不可把人物、來源、字串、條件硬寫死在 runner 裡。
- production namespace 寫入必須有 smoke namespace probe、rollback manifest、quota 確認與人工 gate。

## 目標資料契約

PostgreSQL 目標表群：

- `pipeline_runs`：run profile、input fingerprint、status、summary、canonicalWrites。
- `source_runs`：sourceId、fetch count、seed/card count、timeout、ROI、body-boundary telemetry summary。
- `harvested_pages`：url、title、textHash、bodyStart/bodyEnd、artifactUri、rawBytes、sourcePolicyId。
- `evidence_seeds`：seedId、generalId、angleType、seedTextHash、score JSON、anchor JSON、payload JSON。
- `evidence_cards`：evidenceId、sourceFamily、sourceLayer、quoteHash、locator、anchorEvidence、trust score payload。
- `anchor_passages`：corpusId、layer、locator、textHash、normalizedText、artifactUri。
- `proposal_ledger`：alias/noise/sourceRef/sourceStatus/bodyBoundary residual proposal 與 sandbox outcome。
- `vector_ingestion_records`：provider、namespace、record sha256、source table、upsert/probe/rollback manifest。

Vector record 目標 metadata：

- `recordId`
- `recordType=anchor_passage|evidence_card|event|persona|keyword`
- `schemaVersion`
- `runId`
- `sourceId`
- `sourceFamily`
- `sourceLayer`
- `generalIds`
- `locator`
- `textHash`
- `anchorVerdict`
- `canonicalWrites`
- `payloadUri`

## 推進階段

| 階段 | 目標 | 任務卡 |
|---|---|---|
| M0 | 建立容量基線與落差報告 | `SANGUO-RAGOPS-0001` |
| M1 | Artifact lake 與 manifest/resume 契約 | `SANGUO-RAGOPS-0101`、`SANGUO-RAGOPS-0102` |
| M2 | PostgreSQL schema、adapter、backfill、dual-write | `SANGUO-RAGOPS-0201` 到 `SANGUO-RAGOPS-0204` |
| M3 | Evidence vector export 與 smoke namespace ingestion | `SANGUO-RAGOPS-0301`、`SANGUO-RAGOPS-0302` |
| M4 | 大量試跑 profile、backpressure、治理 runbook | `SANGUO-RAGOPS-0401`、`SANGUO-RAGOPS-0402` |
| M5 | cutover/promotion 決策包 | `SANGUO-RAGOPS-0501` |
| M6 | convergence loop repository 接軌（opt-in） | `SANGUO-RAGOPS-0601` ~ `SANGUO-RAGOPS-0606` |

## M6 任務卡回寫

- `SANGUO-RAGOPS-0601`：定義 convergence loop 走 repository 的 opt-in 合約與資料契約（預設不會改變 JSONL 行為）。
- `SANGUO-RAGOPS-0602`：在 `run_full_roster_convergence_loop.py` 新增 repository 寫入 seam，預設 canonical-only。
- `SANGUO-RAGOPS-0603`：加入 evidence manifest 與 resume 機制（含 body-boundary telemetry 參考與 hash 驗證）。
- `SANGUO-RAGOPS-0604`：加入 convergence dual-write/JSONL parity rehearsal gate，保證 no-write 與 jsonl-only 行為可回放。
- `SANGUO-RAGOPS-0605`：建立 vector smoke linkage 與預算 telemetry，讓回饋走 proposal（非手工數值）。
- `SANGUO-RAGOPS-0606`：更新 runbook、handoff、cutover 決策包，定義禁寫、回滾、觀察窗口與人工放行條件。

目前 0601–0606 皆為 `open`，屬於下一輪實作入口；本計畫文件更新後將作為這批卡片的對應依據與回填據點。後續若這批卡進入 `claim/close`，請在本節補上每張卡的完成證據路徑與驗收結果。

## 驗收門檻

- JSONL artifact count/hash 與 PostgreSQL backfill count/hash 可比對。
- dual-write 模式下 PostgreSQL mirror 不改變 pipeline 原輸出。
- kill/retry 後能用 manifest resume，不需要全目錄暴力掃描。
- 向量 smoke namespace 可 upsert、probe、rollback，且 production namespace 預設不寫入。
- 大量試跑可產出 source-level ROI、file count、raw bytes、resume scan seconds、vector record count、PostgreSQL row count。
- governance regression 至少涵蓋 canonicalWrites、anchor provenance isolation、vector namespace isolation、DB parity。

## 任務卡位置

本計畫已開立 ATM work items 於 `.atm/history/tasks/SANGUO-RAGOPS-*.json`，每張卡均保留 `.atm/history/evidence/SANGUO-RAGOPS-*.json` 作為後續 claim/close 的 evidence package 入口。

# 3KLife NPC Brain 共識摘要（keep.summary）

本檔是 `docs/keep.md` 的執行摘要。每次開始工作先讀這份；遇到重大技術決策或共識衝突，再回讀 `docs/keep.md` 全文。

## 專案定位
- `3klife-npc-brain` 是獨立的 NPC 大腦微服務。
- 對外以 FastAPI 提供 HTTP 介面，服務 Cocos 前端與其他 3KLife 模組。
- 核心目標：提供具人格、關係記憶、天下局勢感知的三國 NPC 對話與決策能力。

## 開發原則
- 資料驅動：邏輯與資料分離，避免硬編碼；資料可落在 JSON / PostgreSQL / 向量索引。
- 模組解耦：服務層、行為決策層、RAG/檢索層清楚分工。
- AI 友善：結構清晰、可讀性高、註解充分，利於人機協作開發。
- 效能治理：用記憶壓縮與上下文管理控制 Token 成本與穩定性。
- 語言規範：全繁體中文與台灣慣用術語。

## 當前技術共識
- 以 Docker 為主開發與測試執行環境（`docker-compose.dev.yml`）。
- NPC 回應需整合三層訊號：
- 武將人格與歷史知識
- 當前局勢
- 玩家與武將互動關係
- 模型有 fallback 鏈，確保服務可用性。
- 變更治理採 ATM（AI-Atomic-Framework），搭配 smoke / regression 驗證。

## 執行提醒
- 新需求先對齊本摘要；若與摘要衝突，先升級 keep 共識再動手。
- 非必要不改動 canonical 真值，優先走 proposal / review / gate 流程。

## 2026-05-26 Scene / 上游資料責任邊界

- `scene-director`、`relationship`、`runtime-story-beat`、`pair linking`、`evidenceRefs`、`angle/classification` 若出錯，預設先視為上游 artifact / pipeline 問題。
- NPC Brain service 只負責通用選卡、資料檢核、診斷、fail-fast、空狀態與 generic guard；不得為單一人物、單一關係、單一角度或 demo case 寫死規則、條件、台詞或用字去遮資料錯誤。
- 若畫面出現怪劇情、錯關係、錯角度，優先回查 runtime profile、relationship edge、story beat、source packet、evidence export 與 pipeline 腳本，再決定 service 是否只需保留通用防呆。
- `evidenceRefs` 應保持 canonical source refs；synthetic / internal ids 不應混入 scene 證據層。

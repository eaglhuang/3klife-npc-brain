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

## 2026-05-26 Scene / 責任區分（摘要）

- 角色分三層：(A) `NPC Brain service`、(B) 上游 `pipeline / artifact`、(C) `HTML / 前端畫面`。
- (A) service：負責通用選卡、資料檢核、`dataStatus` / `fallbackReason` / `evidenceResolution` / debug metadata、fail-fast、完整 payload；禁止為特定人物 / 關係 / demo case 寫死規則、台詞或模板句去遮資料錯誤。
- (B) pipeline：負責 canonical 的 `runtime profile`、`relationship edge`、`runtime-story-beat`、`pair linking`、`angle/classification`、`evidenceRefs`、`source packet / context`；禁止輸出錯 pair、錯 angle、錯 evidence，或把 synthetic / internal ids 混入 `evidenceRefs`。
- (C) HTML：負責顯示、互動、loading、timeout / abort、空狀態、選項聯動；禁止自行補劇情、補人格、補關係。
- 排查順序固定：先查上游資料，再查 service，最後查 HTML。

# 3KLife NPC 大腦服務 - 共識與技術決策記錄 (docs/keep.md)

歡迎使用本文件。此文件為本專案與 AI 協作的最高執行準則與共識紀錄，所有技術決策與設計原則均記錄於此，以便在每次對話開始時進行摘要與遵循。

## 🎯 專案核心定位

`3klife-npc-brain` 是一個獨立的 **NPC 大腦微服務**。它與 Cocos 遊戲前端以及其他 3KLife 服務解耦，主要透過高效能的 HTTP 介面（FastAPI）為遊戲提供具備**武將人格特質、天下大勢感知、玩家關係與互動歷史**的高智能三國 NPC 對話與決策。

---

## 🛠️ 開發與架構原則

1. **資料驅動 (Data-Driven)**：
   - 遊戲邏輯與數據徹底分離。武將人格特質、歷史事件包、關係設定等均由數據（JSON, PostgreSQL, Qdrant 向量索引）管理，避免硬編碼。
2. **模組解耦 (Decoupling)**：
   - 分為 FastAPI 服務層（處理 Web 請求與快取）、LangGraph 行為決策層（控制 NPC 動態行為與資料推進）、以及 RAG 向量檢索層（提供情境語境）。
3. **適合 AI / vibe coding**：
   - 代碼結構保持極度清晰、易於閱讀、添加詳細的繁體中文註解。
4. **性能與 GC 管理**：
   - 包含對話記憶體壓縮機制（`memory_compressor.py`），精準控制 Token 耗用與上下文長度，避免垃圾回收（GC）導致卡頓或崩潰。
5. **全繁體中文與台灣在地術語**：
   - 所有文件、註解、變數命名說明均使用台灣開發術語（如：專案、資料夾、建置、宣告、伺服器、管線、向量檢索）。

---

## 📌 當前重大技術共識

* **Docker 優先原則**：正式開發與測試環境均以 Docker 為主，透過 `docker-compose.dev.yml` 啟動 FastAPI 服務與 Qdrant 向量資料庫。本機 Python 僅用於 IDE 偵錯或 LangGraph dev。
* **三語境整合決策**：NPC 每句回應皆整合：
  1. 武將本體人格與歷史知識
  2. 當前時局變化（天下大勢）
  3. 玩家與武將的雙邊關係（信任、好感與互動日誌）
* **模型回退機制 (Fallback)**：主要採用高品質 LLM (如 Gemini Flash)，並具備 Fallback 鏈（Gemini Flash Lite ➡️ 本地 LLaMA ➡️ 歷史快取回應），確保服務高可用性。
* **治理與變更管理**：本專案使用 `AI-Atomic-Framework` (ATM) 作為 AI 治理與變更管理工具，進行自動化煙霧測試 (Smoke Test) 與回歸測試。

## 2026-05-26 Scene / 責任區分

- 目前 Scene 流程至少有 3 個角色：
  - (A) `NPC Brain service`
  - (B) 上游 `pipeline / artifact`
  - (C) `HTML / 前端畫面`

- (A) `NPC Brain service`
  - 負責：依據既有 artifact 做通用選卡、資料檢核、`dataStatus` / `fallbackReason` / `emptyReason` / `evidenceResolution` / debug metadata 回傳、fail-fast、timeout 保護、完整 payload shape 輸出。
  - 禁止：不得為單一人物、單一關係、單一角度或 demo case 寫死規則、條件、台詞、用字；不得用模板句、人物特判或固定 fallback 掩蓋上游資料錯誤。

- (B) 上游 `pipeline / artifact`
  - 負責：產出 canonical 的 `runtime profile`、`relationship edge`、`runtime-story-beat`、`pair linking`、`angle/classification`、`evidenceRefs`、`source packet / context`、readiness / export 結果。
  - 禁止：不得把 synthetic / internal ids 混進 `evidenceRefs`；不得把錯的 pair linking、錯的 angle、錯的 evidence export、上下文不足的短摘，留給 service 或前端補救。

- (C) `HTML / 前端畫面`
  - 負責：通用顯示、互動、loading 狀態、timeout / abort、欄位空狀態、選項聯動、診斷資訊呈現。
  - 禁止：不得自行判斷人物性格、關係正確性、角度正確性、證據真偽；不得在前端生成旁人感想、小劇場或補寫故事。

- 排查順序固定為：先查 (B) 上游資料，再查 (A) service 的通用選卡與檢核，最後才查 (C) 畫面顯示；禁止顛倒順序，用下游硬補去掩蓋上游錯誤。

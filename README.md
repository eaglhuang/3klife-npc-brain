<!-- doc_id: doc_server_service_0001 -->
# NPC Brain Service

> 文件使用原則：
> - 本文假設你正在使用 standalone `3klife-npc-brain` repo。
> - `<repo-root>` 代表你自己 clone 下來的專案根目錄。
> - 若本文包含啟動或驗證指令，預設以 Docker 為正式開發環境來源；本機 Python / venv 僅用於 IDE debug、LangGraph dev 或臨時工具。

> 本文中的 `<repo-root>` 代表你自己 clone 下來的專案根目錄。

3klife-npc-brain 是獨立的 NPC brain 服務 repo。它不再放在 3KLife monorepo 下面；Cocos / 3KLife 只需要透過 HTTP 呼叫這個服務。

如果你是第一次進這個 repo，建議先看 [最短啟動路徑](./文件/最短啟動路徑.md)。

## 這個 repo 負責什麼

- FastAPI runtime：提供 `/healthz`、`/v1/npc/context-options`、`/v1/npc/keyword-options`、`/v1/npc/dialogue`。
- Sanguo-RAG pipeline：產生 persona、keyword、relationship、event、runtime profile 等 artifacts。
- LangGraph：提供本地 Studio / LangSmith 可看的 graph workflow。
- Vector / governance 工具：維護 Pinecone、Qdrant、governance validator 與 regression harness。

## 推薦啟動方式：Docker Compose

這是拆分後的正式開發環境來源。若 Docker Desktop 已啟動，直接在 repo root 執行：

```powershell
cd <repo-root>
docker compose -f docker-compose.dev.yml up -d --build
```

檢查服務：

```powershell
curl http://127.0.0.1:8765/healthz
docker compose -f docker-compose.dev.yml ps
```

停止服務：

```powershell
docker compose -f docker-compose.dev.yml down
```

## 可選：本機 Python 方式

Docker 是優先路線；如果你要在本機 Python 跑，請使用 Python 3.11，並在 repo root 執行：

```powershell
cd <repo-root>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

WSL2 範例：

```bash
cd <repo-root>
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

## LangGraph dev server

Docker runtime 跑 FastAPI；LangGraph Studio 若要本機開發，可以在 Python 3.11 環境中執行：

```powershell
cd <repo-root>
python -m pip install -U "langgraph-cli[inmem]"
langgraph dev --no-browser
```

預設 API 在 `http://127.0.0.1:2024`。

## 常用 smoke checks

```powershell
cd <repo-root>
docker exec 3klife-npc-brain-dev python -B -m app.http_smoke_test
docker exec 3klife-npc-brain-dev python -B pipelines/sanguo-rag/validate_sanguo_governance.py --dry-run-report
docker exec 3klife-npc-brain-dev python -B pipelines/sanguo-rag/run_sanguo_governance_regression_harness.py --run-profile strict-local --no-write
```

## 與 3KLife 的關係

- `3KLife` 不 import Python 原始碼。
- Cocos 端只連 `http://127.0.0.1:8765`。
- 如果需要更新 artifacts，請在這個 repo 內跑 pipeline，再讓服務讀取 `artifacts/data-pipeline/sanguo-rag/...`。

## 文件入口

- [開發啟動與煙霧測試](./文件/開發啟動與煙霧測試.md)
- [LangGraph Studio 與部署](./文件/LangGraph Studio 與部署.md)
- [向量檢索與資料入庫](./文件/向量檢索與資料入庫.md)
- [資料契約與 Cocos 串接](./文件/資料契約與 Cocos 串接.md)
- [三國人物資料推進流程](./文件/三國人物資料推進流程.md)

<!-- doc_id: doc_server_service_0005 -->
# LangGraph Studio 與部署

> 文件使用原則：
> - 本文假設你正在使用 standalone `3klife-npc-brain` repo。
> - `<repo-root>` 代表你自己 clone 下來的專案根目錄。
> - 若本文包含啟動或驗證指令，預設以 Docker 為正式開發環境來源；本機 Python / venv 僅用於 IDE debug、LangGraph dev 或臨時工具。

> 本文中的 `<repo-root>` 代表你自己 clone 下來的專案根目錄。

這份文件描述 standalone `3klife-npc-brain` 的 LangGraph 使用方式。路徑一律以 repo root `<repo-root>` 為基準。

## 相關檔案

- `langgraph.json`
- `auth.py`
- `langgraph_app/graph.py`
- `langgraph_app/etl_graph.py`
- `langgraph_app/etl_repair_graph.py`
- `langgraph_app/progress_advancement_graph.py`

## 安裝 LangGraph CLI

```powershell
cd <repo-root>
python -m pip install -r requirements.txt
python -m pip install -U "langgraph-cli[inmem]"
```

## `.env` 最小設定

```text
LANGSMITH_API_KEY=<token>
LANGSMITH_TRACING=true
LANGCHAIN_TRACING_V2=true
LANGSMITH_PROJECT=3KLife-npc-brain-local
LANGSMITH_DEPLOYMENT_NAME=3klife-npc-brain-external-test
NPC_BRAIN_DEPLOY_API_KEY=<shared-key>
NPC_BRAIN_DEPLOY_IDENTITY=npc-brain-external-tester
```

## 啟動 dev server

```powershell
cd <repo-root>
langgraph dev --no-browser
```

預設：

- API：`http://127.0.0.1:2024`
- Studio：LangSmith Studio 使用 `baseUrl=http://127.0.0.1:2024`

## Studio 連線

1. 開啟 LangSmith Studio。
2. `Base URL` 填 `http://127.0.0.1:2024`。
3. Header 加上 `X-API-Key: <NPC_BRAIN_DEPLOY_API_KEY>`。
4. 按 `Connect`。

## 可用 graphs

| graph | 用途 |
|---|---|
| `npc_brain_graph` | NPC dialogue smoke / provider route |
| `sanguo_etl_graph` | ETL / repair-review / pilot queue |
| `sanguo_etl_repair_graph` | repair-review 與 readiness refresh |
| `progress_advancement_graph` | 三國資料推進總控 |

## 外部測試腳本

```bash
cd <repo-root>
bash ./run-temporary-external-test.sh
```

## Deployment

```bash
cd <repo-root>
langgraph deploy --name "$LANGSMITH_DEPLOYMENT_NAME"
```

若你用固定 venv，可改用該 venv 的 `langgraph` binary，但工作目錄仍必須是 standalone repo root。

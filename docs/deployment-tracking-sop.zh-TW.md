# 3KLife NPC Brain 部署追蹤 SOP

這份 SOP 的目的，是在服務重新部署後，快速判斷問題到底出在哪一層：

- 舊部署還在跑
- 新部署已上線，但資料快照還沒刷新
- 後端已更新，但前端還在吃舊快取或舊資產

本文件以目前 `npc-brain` 服務為準，適用於 Render + GitHub Pages 的組合。

## 1. 先看健康檢查

先打遠端健康檢查：

```text
GET https://threeklife-npc-brain.onrender.com/healthz
```

重點看這幾個欄位：

- `deployment.renderGitCommit`
- `deployment.githubSha`
- `deployment.buildSha`
- `deployment.renderServiceName`
- `runtimeSnapshots.personaRoot.mtime`
- `runtimeSnapshots.runtimeProfileRoot.mtime`
- `runtimeSnapshots.eventRoot.mtime`
- `runtimeSnapshots.readyEventsFile.mtime`
- `runtimeSnapshots.sourceEventPacketsFile.mtime`

### 判斷

- `renderGitCommit` 有更新，但畫面還是舊的
  - 多半是資料快照、前端快取，或 GitHub Pages 還在吃舊資產。
- `renderGitCommit` 沒更新
  - 表示 Render 還沒真的 redeploy 到新版本。
- `renderGitCommit` 已更新，且 `runtimeSnapshots` 的 `mtime` 也已更新
  - 後端部署與資料檔都已刷新，問題通常在前端或瀏覽器快取。

## 2. 再看資料快照是不是新鮮

如果 `/healthz` 顯示的 `runtimeSnapshots` 還是舊時間，表示服務雖然啟動了，但讀到的是舊檔案或舊快照。

### 先檢查這幾個來源

- `artifacts/data-pipeline/sanguo-rag/extracted/runtime-general-profiles/`
- `artifacts/data-pipeline/sanguo-rag/extracted/events/events.jsonl`
- `artifacts/data-pipeline/sanguo-rag/extracted/source-event-packets/source-event-packets.jsonl`
- `artifacts/data-pipeline/sanguo-rag/extracted/persona-cards/`

### 判斷

- 檔案時間很新，但 `/healthz` 仍顯示舊 `mtime`
  - 代表服務快取還沒刷新，或遠端部署沒吃到新檔。
- 檔案時間本身就舊
  - 代表上游 pipeline 還沒真的把新資料產出。

## 3. 再看前端是不是還在吃舊資料

前端畫面如果看起來不動，先分三件事查：

### A. Network 是否有打到新 API

確認前端是否有發出：

- `GET /v1/npc/narrative-profile?generalId=...`
- `POST /v1/npc/scene-director`
- `POST /v1/npc/scene-illustration`

如果 API 回 200，但畫面沒變，通常不是後端壞掉，而是前端顯示層或資料綁定層有問題。

### B. Console 是否有資產 404

如果看到像下面這種錯誤：

- `assets/resources/sprites/generals/zhao_yun_portrait.png 404`
- `assets/resources/sprites/generals/guan_yu_portrait.png 404`

表示是 GitHub Pages 的靜態資產缺檔，不一定代表後端有問題。

### C. 前端是否仍在讀舊快照

如果頁面拿到的 API 有新資料，但畫面仍呈現舊角色內容，通常是：

- 瀏覽器快取沒清
- GitHub Pages 還沒更新靜態頁
- 前端 JS 還引用舊版 API 或舊版資產路徑

## 4. 舊部署、舊快照、前端舊資料的快速分流

### 舊部署

特徵：

- `/healthz` 的 `deployment.renderGitCommit` 沒變
- Render 上的部署時間沒刷新

處理：

- 觸發 Render redeploy
- 確認 GitHub Actions 的排程 webhook 是否正常

### 舊快照

特徵：

- `deployment.renderGitCommit` 已變
- 但 `runtimeSnapshots.*.mtime` 仍舊
- API 回應裡的關係、證據數量還是舊的

處理：

- 重新產生上游資料檔
- 確認 pipeline 有把新檔寫到正確路徑
- 如有快取層，強制清快取或重新載入

### 前端舊資料

特徵：

- API 已回新資料
- 但頁面顯示不變
- Console 可能有 404 或其他靜態資產錯誤

處理：

- 清瀏覽器快取
- 重新部署 GitHub Pages
- 確認前端資產路徑與檔名

## 5. 本專案目前的建議排查順序

1. 先查 `/healthz` 的 `deployment.renderGitCommit`。
2. 再查 `/healthz` 的 `runtimeSnapshots.*.mtime`。
3. 再用瀏覽器 DevTools 看 Network 是否命中新 API。
4. 最後看 Console 是否有 404 或 JS 錯誤。

## 6. 定時部署建議

目前已加入定時 redeploy workflow，概念上是：

- 每天固定時間觸發 Render deploy hook
- 讓服務定期刷新遠端資料與快取

你還需要在 GitHub Repo Secret 裡補一個：

- `RENDER_DEPLOY_HOOK_URL`

只要這個 secret 正確，排程就可以自動觸發。

## 7. 一句話判斷法

- `commit 沒變` = 舊部署
- `commit 變了但 mtime 沒變` = 舊快照
- `API 變了但畫面沒變` = 前端還在吃舊資料或舊資產

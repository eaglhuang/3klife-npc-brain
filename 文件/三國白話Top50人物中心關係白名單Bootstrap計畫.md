<!-- doc_id: doc_sanguo_bootstrap_0001 -->
# 三國白話 Top50 人物中心關係白名單 Bootstrap 計畫

## 摘要

本計畫新增一條獨立的 `人物中心白話 bootstrap lane`，目標是先用白話《三國演義》為前 50 名三國人物建立第一份高可信關係白名單候選，再將結果接回既有 `relationship trust-zone` 的 `stable-90 / skill-reviewed-95 / human-locked-100 / whitelist-blacklist` 治理流程。

這條 lane 不再沿用目前「先從大量句子猜 pair，再逐句驗證」的做法，而是改成「以單一人物為中心」做批次抽取：

1. 每次只處理一名焦點人物。
2. 從白話《三國演義》相關段落中，抽取該人物與 top50 其他人物的硬關係。
3. 先形成高信任 bootstrap 候選，再經過去重、對稱化、衝突檢查、review queue 與人工鎖定。

這條 lane 的成功條件不是一次取代全部關係管線，而是：

- 快速建立前 50 名人物的第一份可信關係基線。
- 明顯降低目前 top50 關係審核中的低價值殘值與錯誤 pair。
- 建立一套可以按 `wave-001 / wave-002 / ...` 擴張到全 roster 的批次流程。

---

## 一、問題定義與設計原則

### 1.1 為什麼要新增人物中心 lane

目前關係 trust-zone 的主路徑仍偏向「句子中心」：

1. 先從來源句子推導大量候選 pair。
2. 再用 semantic review / skill review 驗證單句是否支持該 pair。

這個方式在 top50 關係治理上有三個成本：

- `pair 組合爆炸`：人物一多，錯誤 pair 會先被生成，再花大量算力排除。
- `句子視角過窄`：單句模型常回 `not_enough_context`，因為它只看一小句，不是以人物整體關係為單位閱讀。
- `時段資訊難保留`：像君臣與陣營關係常有時間段，如果沒有先站在人物中心整理，容易混成靜態 pair。

白話《三國演義》本身已經具備以下優勢：

- 句意比文言文明確，人物與關係 cue 容易辨識。
- repo 中已存在 `sanguoyanyi-baihua-zh-tw` anchor corpus，可直接當可讀性強的 bootstrap 來源。
- 關係抽取的第一批目標不是全知識圖譜，而是 top50 的硬關係基線，完全適合走「人物中心」。

### 1.2 這條 lane 的定位

本 lane 的定位是：

- `高信任 bootstrap 候選來源`
- `top50 / topN 關係白名單加速器`
- `review queue 縮小器`

它不是：

- 直接正式 A 證據寫入 canonical
- 新的一套獨立白名單制度
- 取代既有 trust-zone / reviewer / human gate

### 1.3 設計原則

1. `資料驅動優先`
   - 不把人物特例寫死在腳本。
   - 關係型別、批次大小、stage 映射、驗收門檻都放在 policy / contract 中。

2. `人物中心抽取，句子作為證據`
   - skill 的工作是讀人物相關段落後，輸出關係與其證據。
   - 不是只回抽象關係，必須附原文句子與章回定位。

3. `先 bootstrap，後治理鎖定`
   - bootstrap 產物先進候選區，不直接升 `human-locked-100`。
   - 後續仍由 reviewer / human 決策進白黑名單。

4. `批次可擴張`
   - 先 `top50`，再 `next50`，每批固定 50。
   - 每一波都可重跑、可續跑、可回放。

5. `全程 `canonicalWrites=false``
   - 直到進入既有人工決策與白黑名單鏡像流程前，bootstrap lane 只產 proposal / candidate / evidence。

---

## 二、關係範圍與證據地位

### 2.1 第一批關係範圍

第一批只做硬關係：

- `ruler_subject`
- `faction_membership`
- `parent_child`
- `adoptive_parent_child`
- `spouse`
- `sibling`
- `sworn_sibling`

第一批不納入：

- `friend`
- 情感關係
- 軟聯盟 / 欣賞 / 師承 / 宿敵等高歧義關係

原因很簡單：第一波目標是收斂高確定性的基線，而不是擴大全部關係覆蓋。

### 2.2 證據等級

白話 bootstrap 產物先定義為：

- `高信任 bootstrap 候選`
- `可接 review queue 的基線資料`
- `可提升 reviewer / human 審核效率的候選集合`

它不直接等於：

- `A-history`
- `human-locked-100`
- 最終 canonical 白名單

### 2.3 與既有 trust-zone 的關係

本計畫不建立第二套白黑名單制度。

最終仍沿用既有：

- `relationship-trust-whitelist`
- `relationship-trust-blacklist`
- `human-locked-100`
- `human-rejected-0`

bootstrap lane 的責任是把前 50 名人物的核心硬關係盡快整理出來，縮小後續 skill review / human review 的工作量，而不是繞過治理。

---

## 三、資料流與流程

## 3.1 入口資料

### 入口一：焦點人物名單

沿用現有 `build_famous_person_ranking.py` 產出的 top50 名單，作為 `wave-001` 的焦點集合。

### 入口二：白話 anchor corpus

沿用現有 `sanguoyanyi-baihua-zh-tw` corpus 與 anchor passage registry。

這代表本 lane 不需要新抓外部網站；第一版只依賴 repo 內已整理完成的白話譯本 passage 與既有名單輸入。

## 3.2 人物中心 job 流程

每次執行以「一名人物 = 一個 bootstrap job」為單位：

1. 取出 `focusGeneralId`
2. 找到該人物在白話 corpus 中的相關 passages
3. 把 top50 其餘人物作為候選對象集合
4. 讓 skill 判斷該人物與候選人物之間是否存在硬關係
5. 對每筆關係必須輸出：
   - 關係型別
   - 關係方向
   - 證據原文
   - 章回/locator
   - 時段說明
   - 信心分數

## 3.3 批次流程

每一波固定走以下 8 步：

1. 產生 top50 人物中心 job 清單
2. 為每個人物準備白話 passage bundle
3. skill 抽取該人物的多筆關係候選
4. 合併成 `bootstrap relation candidates`
5. 做去重、對稱化、衝突檢查、時段拆分
6. 產出第一版 bootstrap 白名單候選
7. 對高分候選接 reviewer / human review
8. 通過後再鏡像進既有 whitelist / trust-zone 固定區

## 3.4 後續擴張方式

當 `wave-001 = top50` 驗收通過後，後續波次固定為：

- `wave-002 = next50`
- `wave-003 = next50`
- 直到 full roster 掃完

每一波都必須保留：

- input manifest
- job 清單
- bootstrap 候選輸出
- 衝突報告
- reviewer queue
- human decision 結果
- wave summary

---

## 四、介面與資料契約

## 4.1 人物中心 job 輸入契約

每個 bootstrap job 的 JSON 形狀如下：

```json
{
  "jobId": "baihua-bootstrap:wave-001:zhang-fei",
  "focusGeneralId": "zhang-fei",
  "focusNameZhTw": "張飛",
  "candidateCounterpartIds": ["liu-bei", "guan-yu", "zhao-yun"],
  "allowedRelationshipTypes": [
    "ruler_subject",
    "faction_membership",
    "parent_child",
    "adoptive_parent_child",
    "spouse",
    "sibling",
    "sworn_sibling"
  ],
  "sourceCorpusId": "sanguoyanyi-baihua-zh-tw",
  "passageRefs": ["baihua-120hui:001:passage-00032"],
  "canonicalWrites": false
}
```

## 4.2 skill 輸出契約

skill 輸出必須以關係陣列表示，每筆 relation 需同時包含答案與證據：

```json
{
  "focusGeneralId": "zhang-fei",
  "relationships": [
    {
      "fromId": "liu-bei",
      "toId": "zhang-fei",
      "relationshipType": "sworn_sibling",
      "relationshipDirection": "bidirectional",
      "timeScopeZhTw": "桃園結義後至張飛去世前",
      "evidenceQuoteZhTw": "玄德、關羽、張飛三人誓同生死。",
      "chapterRef": "第一回",
      "sourcePassageRef": "baihua-120hui:001:passage-00032",
      "confidence": 0.97,
      "reasonZhTw": "原文明確描述三人結義，屬硬關係。",
      "canonicalWrites": false
    }
  ],
  "canonicalWrites": false
}
```

### skill 輸出強制規則

1. 只允許輸出 `allowedRelationshipTypes` 中的關係。
2. 每筆關係必須附原文句子。
3. 每筆關係必須附章回與 passage ref。
4. 沒有證據句不得輸出關係。
5. 若關係不確定，必須省略，不可猜測。

## 4.3 bootstrap 合併後 JSONL 契約

合併後的候選輸出形狀如下：

```json
{
  "trustKey": "relationship|sworn_sibling|liu-bei|zhang-fei",
  "sourceMode": "top50-baihua-bootstrap",
  "bootstrapStage": "bootstrap-candidate",
  "supportCount": 2,
  "focusGeneralIds": ["liu-bei", "zhang-fei"],
  "evidenceQuotes": ["玄德、關羽、張飛三人誓同生死。"],
  "chapterRefs": ["第一回"],
  "timeScopeZhTw": "桃園結義後",
  "confidenceAggregate": 0.965,
  "conflictFlags": [],
  "canonicalWrites": false
}
```

### `bootstrapStage` 定義

- `bootstrap-candidate`
- `review-ready`
- `conflicted`
- `rejected`

## 4.4 trust-zone 對接原則

對接既有 trust-zone 時遵守以下規則：

1. 不直接寫 `human-locked-100`
2. 可先進新的 `bootstrap review lane`
3. reviewer / human 決策後，才透過既有 `force-whitelist` / `force-blacklist` 契約進 skip-index

也就是說，bootstrap lane 是新的高信任來源，不是新的最終裁決權。

---

## 五、實作變更

## 5.1 新增人物中心 bootstrap runner

新增一支人物中心 bootstrap runner，職責如下：

1. 載入 top50 名單
2. 依人物建立 bootstrap jobs
3. 從白話 anchor passages 準備輸入
4. 送進 skill
5. 收集輸出與寫回候選 JSONL

這支 runner 不直接做人類鎖定，只產生高信任候選與 review-ready 輸入。

## 5.2 新增 bootstrap merge / normalize runner

新增一支 merge / normalize runner，職責如下：

1. 合併相同 pair 的多次抽取
2. 做對稱化：
   - `spouse`
   - `sibling`
   - `sworn_sibling`
3. 保留非對稱關係：
   - `ruler_subject`
   - `faction_membership`
   - `parent_child`
   - `adoptive_parent_child`
4. 依 `timeScopeZhTw` 拆開多時段關係
5. 產出 `confidenceAggregate`

## 5.3 新增 bootstrap conflict checker

新增 conflict checker，至少處理以下互斥規則：

1. `spouse` vs `parent_child`
2. `parent_child` vs `adoptive_parent_child`
3. `sibling` vs `sworn_sibling`
4. 同一 pair 同時出現互斥關係

第一版只要做到「自動標記 conflicted 與產出衝突報告」即可，不要求全自動裁決。

## 5.4 新增 bootstrap-to-trust-zone adapter

新增 adapter，把高信任 bootstrap 候選轉成既有 review queue 可讀格式：

- 進入既有 reviewer queue
- 生成繁中人工審核表
- 審核通過後走既有 `human-locked-100`
- 審核拒絕後走既有 `human-rejected-0`

---

## 六、治理與既有制度對齊

### 6.1 沿用現有 stage

本 lane 不發明新的最終 stage，最終仍沿用既有：

- `stable-90`
- `skill-reviewed-95`
- `human-locked-100`
- `human-rejected-0`

### 6.2 白黑名單仍只有一套

規劃書必須明確寫出：

1. bootstrap 只是新來源，不改變最終白黑名單治理權限。
2. `human-locked-100` 仍是唯一固定白名單鏡像。
3. `human-rejected-0` 仍是硬黑名單。

### 6.3 與既有 skip-index 相容

bootstrap lane 的審核結果最終仍回寫到現有：

- `relationship-trust-whitelist`
- `relationship-trust-blacklist`

不新增第二套 skip-index 或第二套名單格式。

---

## 七、批次擴張策略

## 7.1 波次設計

固定批次如下：

- `wave-001 = top50`
- `wave-002 = next50`
- `wave-003 = next50`

每一波固定 50 人，不混用自由大小批次。

## 7.2 每波輸出

每波都必須產出：

- `wave summary`
- `job manifest`
- `bootstrap candidates`
- `new whitelist candidates`
- `new blacklist candidates`
- `conflict report`
- `coverage report`

## 7.3 進下一波條件

只有當上一波達到驗收門檻，才進下一波。

建議驗收門檻：

1. 核心硬關係 coverage 達標
2. 衝突率低於門檻
3. reviewer / human 可處理量在可控範圍內
4. 白名單與黑名單鏡像流程正常

---

## 八、測試與驗收

## 8.1 靜態測試

1. skill 輸入 schema 驗證
2. skill 輸出 schema 驗證
3. trustKey 組裝與方向正規化
4. 時段欄位與章回欄位必填檢查

## 8.2 合併測試

1. 同 pair 多引用句合併
2. `spouse`、`sibling`、`sworn_sibling` 對稱化
3. `ruler_subject`、`faction_membership` 非對稱保留

## 8.3 衝突測試

至少驗以下案例：

1. 孫堅 / 吳國太不得同時存在 `spouse` 與 `parent_child`
2. 呂布 / 丁原可標為 `adoptive_parent_child`，不可誤併為真實親子
3. 馬超這類轉陣營人物可同時存在不同時段的關係

## 8.4 top50 pilot 驗收

top50 pilot 視為成功，至少要滿足：

1. 產出第一版 top50 硬關係白名單候選
2. 能自動抓出衝突與重複
3. 能輸出繁中人工審核表
4. 通過人工審核的關係能正確進既有 whitelist

## 8.5 全量擴張驗收

全量擴張驗收重點：

1. `wave-by-wave` 可重跑
2. 可續跑
3. 可回放
4. 每批 50 人都能沿用同一格式與同一治理流程

---

## 九、建議任務拆分

| 任務卡 | 主題 | 產出 |
| --- | --- | --- |
| `SANGUO-BOOTSTRAP-0001` | 規劃書與資料契約定稿 | 本文件、job/output schema |
| `SANGUO-BOOTSTRAP-0101` | top50 bootstrap job builder | top50 job manifest |
| `SANGUO-BOOTSTRAP-0102` | 白話 passage bundler | focusGeneralId -> passage bundle |
| `SANGUO-BOOTSTRAP-0201` | 人物中心 skill 契約與 runner | skill input/output、runner |
| `SANGUO-BOOTSTRAP-0202` | bootstrap merge / normalize | merged candidate JSONL |
| `SANGUO-BOOTSTRAP-0203` | conflict checker | conflict report |
| `SANGUO-BOOTSTRAP-0301` | bootstrap-to-trust-zone adapter | review lane adapter |
| `SANGUO-BOOTSTRAP-0302` | reviewer / human markdown | 繁中人工審核表 |
| `SANGUO-BOOTSTRAP-0401` | top50 pilot rehearsal | wave-001 summary |
| `SANGUO-BOOTSTRAP-0501` | next50 batch protocol | wave-002+ SOP |

---

## 十、預設與假設

1. 規劃書採新獨立文件，不直接覆寫《三國資料管線低人工自動化計畫書》。
2. 第一版只依賴白話《三國演義》與 top50 名單，不把外部網站列為 bootstrap 主來源。
3. 第一版不納入 `friend` 與更軟的情感關係。
4. 第一版不直接把 bootstrap 輸出視為正式 A / 100，而是視為高信任 bootstrap 候選。
5. 第一版人工審核文件維持全繁體中文，沿用既有決策檔與白黑名單操作語意。
6. 第一版優先追求可信基線與 reviewer 範圍縮小，不追求一次覆蓋全部人物與全部關係。

---

## 十一、ATM 任務卡開立狀態

2026-05-24 已依本規劃書於 `.atm/history/tasks/` 開立以下 ATM work items，後續可直接依 ATM 流程執行 `reserve -> promote -> claim -> close`：

- `SANGUO-BOOTSTRAP-0001`：三國白話 bootstrap 規劃書與資料契約定稿
- `SANGUO-BOOTSTRAP-0101`：Top50 bootstrap job builder
- `SANGUO-BOOTSTRAP-0102`：白話 passage bundler
- `SANGUO-BOOTSTRAP-0201`：人物中心 skill 契約與 runner
- `SANGUO-BOOTSTRAP-0202`：bootstrap merge / normalize
- `SANGUO-BOOTSTRAP-0203`：bootstrap conflict checker
- `SANGUO-BOOTSTRAP-0301`：bootstrap-to-trust-zone adapter
- `SANGUO-BOOTSTRAP-0302`：reviewer / human 繁中審核表
- `SANGUO-BOOTSTRAP-0401`：top50 pilot rehearsal
- `SANGUO-BOOTSTRAP-0501`：next50 batch protocol

每張任務卡都已建立對應的 `.atm/history/evidence/SANGUO-BOOTSTRAP-*.json` stub，方便後續在 close 前補齊 evidence package。

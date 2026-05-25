<!-- doc_id: doc_baihua_focus_skill_contract_0001 -->
# 白話人物中心 Skill 契約（SANGUO-BOOTSTRAP-0201）

## 目的

本契約定義 `人物中心 relationship extraction` 的輸入與輸出，供 `run_baihua_focus_relationship_runner.py` 與後續 reviewer/human lane 共用。  
本 lane 僅輸出候選，不得直接寫入最終白名單；`canonicalWrites` 一律為 `false`。

## Input（單一人物）

欄位對照：

1. `focusGeneralId`：焦點人物 ID
2. `focusNameZhTw`：焦點人物繁中名稱
3. `candidateCounterpartIds`：可配對人物集合（top50 其餘人物）
4. `passages[]`：白話段落清單，至少包含：
   - `locator`
   - `chapterRef`
   - `normalizedText`
   - `personIds[]`
5. `allowedRelationshipTypes`：允許關係型別（由 policy 控制）
6. `canonicalWrites=false`

## Output（單一人物）

欄位對照：

1. `focusGeneralId`
2. `relationships[]`
3. `canonicalWrites=false`

其中 `relationships[]` 每筆必備欄位：

1. `fromId`
2. `toId`
3. `relationshipType`
4. `relationshipDirection`（`directed` / `bidirectional`）
5. `timeScopeZhTw`
6. `evidenceQuoteZhTw`
7. `chapterRef`
8. `sourcePassageRef`
9. `confidence`
10. `reasonZhTw`
11. `canonicalWrites=false`

## 強制規則

1. 只可輸出 `allowedRelationshipTypes` 內的型別。
2. 每筆關係都必須帶 `evidenceQuoteZhTw` 與 `sourcePassageRef`。
3. 若無對應段落證據，該關係不得輸出（可進 unresolved 報表）。
4. 本 runner 不進行最終裁決，不得寫入 `human-locked-100`。
5. 本 runner 產物只能作為 `bootstrap-candidate / review-ready` 上游輸入。

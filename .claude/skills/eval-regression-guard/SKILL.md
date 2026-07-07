---
name: eval-regression-guard
description: "任何影響檢索行為的改動（prompt 形狀、權重、tokenizer 字典、pipeline 步驟、chunker）合入前，必須跑 80q baseline before/after 並用 regression test 鎖住關鍵形狀. Use when 修改 wenji 的 search/eval/ingest tokenization 相關程式碼，或調整任何 LLM prompt template 時."
---

# Eval Regression Guard

> 目標讀者：接手本專案的中階工程師或 Haiku/Sonnet 級模型（零背景假設，全量步驟）。
> 易變事實標注日期；命令於 2026-07-07 實測。

檢索品質的回歸是**靜默的**：程式碼全綠、測試全過、分數掉 10 個百分點。本專案已付過三次學費，規則如下。

## 三道傷疤（為什麼有這份規則）

1. **QueryRewriter prompt 形狀回歸 -10pp**（v0.3.6.1 修復）：把 rewrite prompt 從「keyword groups `|` 分隔」改成「vector-friendly 自然語句」，單元測試全綠，80q baseline 從 77.5% 掉到 67.5%。修法：改回 keyword 形狀 + 用 `test_default_prompt_template_targets_keyword_form_aligned_with_upstream` 把 prompt 形狀鎖進 regression test。教訓：**prompt template 是行為，不是文案** — 改動等同改演算法。
2. **jieba 字典殺人名**（commit `304a592`）：STOPWORDS 誤含單字「拿」，「約拿」被切成「約」+ 被停用的「拿」→ Q33 檢索 miss。修法：從 baseline miss 題反推，移除誤殺停用詞 + `add_word` 補 12 個 OOV 詞。教訓：**tokenizer/字典改動必跑 baseline**，miss 題清單是字典補丁的來源。
3. **計數池與過濾池不一致**（commit `49a6722`）：facet 計數用全量 BM25 召回、點擊過濾只看 hybrid top-50 → 顯示 9 筆點進去剩 8 筆。教訓：**同一畫面的計數與結果必須出自同一份候選池**；現行 pool=50 是已知 trade-off，ranking 重構時重新評估。

## 標準流程（逐步）

1. **確認改動是否觸發本 guard**：碰到以下任一即觸發 — `src/wenji/search/`、`src/wenji/eval/`、`src/wenji/ingest/jieba_setup.py`、任何 prompt template 字串、chunk 策略參數、RRF/entity/intent 權重。純 docs/web UI 樣式改動不觸發。
2. **準備 parity db**（本機跑，比 prod VPS 快一個量級；2026-07-07 起 config 為 SSOT）：
   ```bash
   cd ~/Projects/notoriouslab/logos
   # 照 config/wenji_ingest.yaml 註解：先暫移 breadoflife/ 與 2026BOL/ 出 articles/
   wenji rebuild articles --db data/wenji.db --config config/wenji_ingest.yaml
   ```
3. **跑 before baseline**（改動前的 HEAD）：起 `wenji serve --db <parity.db>`，跑 `wenji eval run-benchmark`（80 題 v2 baseline，對 running serve）。記下 pass@3 partial+ 總分與 miss 題清單。也可用 `scripts/run_wenji_r0_baseline.sh`（含 objective sanity gate，前置條件見 script 開頭註解）。
4. **套改動、跑 after** — 同一顆 db、同一命令。
5. **Keep/Discard 判定**（G4 實驗門控）：
   - after ≥ before：Keep，並到第 6 步
   - after < before：Discard 或迭代；**禁止**「分數掉一點但邏輯上更乾淨所以保留」— 傷疤一就是這樣進 repo 的
   - 逐題 diff：總分持平但 miss 題換人也要看（可能 A 題修好、B 題弄壞）
6. **鎖形狀**：改動涉及 prompt template / 字典 / 權重時，加 regression test 斷言關鍵形狀（參考 `tests/wenji/test_search_rewrite.py:121` `test_default_prompt_template_targets_keyword_form_aligned_with_upstream` 的寫法：斷言模板含關鍵結構標記，而非全文比對）。
7. **對照組紀錄**：baseline 分數寫進 PR description（before → after），下一個人才有對照點。目前公認基準：**77.5% pass@3 partial+（rewrite-off，2026-05 建立）**。

## 何時不用本技能 + 替代

- 改動不碰檢索行為（docs、CI、web 樣式、packaging）→ 走一般 G3 即可
- 全新 eval 題庫設計 → 這是 eval 本身的變更，baseline 對照失義，走 spectra propose 討論
- prod 部署驗證 → 用 `wenji doctor` + 4-query 冒煙（見 logos repo `prod-mode3-migrate-and-rebuild` tasks），不是本技能

## 出處與維護

傷疤出處：CHANGELOG v0.3.6.1 QueryRewriter 段、commit `304a592`（jieba）、commit `49a6722`（facet pool）。規則化：2026-07-07 /distill。基準分數與 parity db 流程屬易變事實 — 每次大版本後驗證本檔命令仍可跑。

"""Prompt templates for :mod:`wenji.ask`."""

from __future__ import annotations

ASK_PROMPT = """你是一位嚴謹的繁體中文知識助理。必須只依照下面列出的「來源」回答使用者的問題。

規則：
1. 只能引用來源內容；禁止加入未列出的事實或自由發揮的推論。
2. 若來源中沒有足夠資訊回答此問題，必須回覆「資料中未提及」，不要捏造答案。
3. 回答中參照特定段落時，使用 `[1]`、`[2]` 等編號對應「來源」序號；不要引用未列出的來源。
4. 用簡潔的 Markdown 撰寫，使用繁體中文，避免冗長前言或自我介紹。
5. 數字、金額、天數、百分比必須照抄來源條文原文，不得換算、四捨五入或概括；來源同時列出多種情境時，只回答與問題相符的那一種。

問題：
<query>{query}</query>

來源：
<sources>{sources}</sources>

請依規則作答：
"""

#: Condenses a follow-up turn into a self-contained retrieval query.
#:
#: Output shape is a *natural question*, not the ``|``-separated keyword groups
#: used by the query rewriter removed in 0.5.0. That rewriter expanded a query
#: for a pipeline with a different candidate mix; this one only resolves
#: pronouns and ellipsis, and its output feeds the same hybrid search where the
#: vector channel is what actually retrieves (measured: every document in the
#: policy corpus except the one oversized file ranks 1 on a spoken-form
#: question). The shape is locked by a regression test.
FOLLOWUP_REWRITE_PROMPT = """你的任務是把使用者的追問改寫成一句可獨立檢索的問題。

規則：
1. 補上追問中省略的主體（例如「那病假呢？」在談婚假的脈絡下應改寫為「病假可以請幾天？」）。
2. 只輸出改寫後的那一句問題，不要加解釋、前言、引號或編號。
3. 保留原問題的專有名詞與數量詞；不要自行添加對話中沒有出現的條件。
4. 若追問本身已經可獨立檢索，原句輸出即可。
5. 用繁體中文輸出。

對話紀錄：
<history>{history}</history>

追問：
<followup>{followup}</followup>

改寫後的問題：
"""

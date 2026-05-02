---
title: ONNX 模型在 CPU 上的推論
pubDate: 2024-09-30
tags: [onnx, 推論, cpu, 教學]
description: 為什麼很多嵌入模型直接走 CPU 也夠用。
---

許多向量檢索場景每秒只跑幾次嵌入，CPU 推論就夠快——尤其是經過 INT8 量化的模型。

onnxruntime 的 CPUExecutionProvider 在 1024-dim 的句子向量上，每次 forward 通常落在 50–200ms 之間。如果把 thread 數鎖到 1，推論會是 deterministic：對同樣輸入產生 byte-identical 輸出，這是保證 rebuild 能對齊的關鍵。

```python
import onnxruntime as ort
opts = ort.SessionOptions()
opts.intra_op_num_threads = 1
opts.inter_op_num_threads = 1
sess = ort.InferenceSession("model.onnx", providers=["CPUExecutionProvider"], sess_options=opts)
```

當你發現 vector 跑兩次有微小差異，第一個要檢查的就是 thread 數。GPU 同樣有不確定性問題，但 CPU 單 thread 是最便宜的決定論方式。

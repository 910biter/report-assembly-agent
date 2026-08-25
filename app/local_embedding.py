"""Lazy CPU embedding runtime, independent from the generation server."""
from __future__ import annotations

import threading

from app.config import settings


class LocalEmbeddingRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None

    def _load(self):
        if self._model is not None:
            return self._tokenizer, self._model
        with self._lock:
            if self._model is not None:
                return self._tokenizer, self._model
            import torch
            from transformers import AutoModel, AutoTokenizer

            torch.set_num_threads(max(1, int(settings.embedding_cpu_threads)))
            model_path = str(settings.embedding_model_path or settings.embedding_model)
            tokenizer = AutoTokenizer.from_pretrained(
                model_path, padding_side="left", local_files_only=True,
            )
            model = AutoModel.from_pretrained(
                model_path, local_files_only=True, dtype=torch.float32,
            ).to("cpu").eval()
            self._tokenizer, self._model = tokenizer, model
        return self._tokenizer, self._model

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        import torch
        import torch.nn.functional as functional

        tokenizer, model = self._load()
        values = [str(text or "") for text in texts]
        if query:
            instruction = str(settings.embedding_query_instruction or "").strip()
            if instruction:
                values = [f"Instruct: {instruction}\nQuery:{text}" for text in values]
        results: list[list[float]] = []
        batch_size = max(1, int(settings.embedding_batch_size))
        with torch.inference_mode():
            for start in range(0, len(values), batch_size):
                batch = tokenizer(
                    values[start:start + batch_size],
                    padding=True,
                    truncation=True,
                    max_length=max(128, int(settings.embedding_max_length)),
                    return_tensors="pt",
                )
                output = model(**batch)
                mask = batch["attention_mask"]
                if bool((mask[:, -1].sum() == mask.shape[0]).item()):
                    pooled = output.last_hidden_state[:, -1]
                else:
                    sequence_lengths = mask.sum(dim=1) - 1
                    pooled = output.last_hidden_state[
                        torch.arange(mask.shape[0]), sequence_lengths
                    ]
                normalized = functional.normalize(pooled, p=2, dim=1)
                results.extend(normalized.float().cpu().tolist())
        return results

    def health(self) -> dict:
        from pathlib import Path

        path = Path(str(settings.embedding_model_path or settings.embedding_model))
        return {
            "backend": "transformers-cpu",
            "model": str(path),
            "available": path.joinpath("config.json").exists(),
            "loaded": self._model is not None,
        }


local_embedding_runtime = LocalEmbeddingRuntime()

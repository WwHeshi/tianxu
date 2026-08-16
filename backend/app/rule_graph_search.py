"""Hybrid exact, BM25 and local-vector retrieval for rule graph searches."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import log
from pathlib import Path
from threading import Lock
from typing import Protocol

import numpy as np

from .config import (
    rule_graph_embedding_enabled,
    rule_graph_embedding_model,
    rule_graph_embedding_model_path,
)
from .graph_store import GraphRuleSummary, normalize_graph_key

BM25_RECALL_LIMIT = 30
VECTOR_RECALL_LIMIT = 30
SEARCH_RESULT_LIMIT = 5
RRF_RANK_CONSTANT = 5
RRF_SCORE_SCALE = 1_000_000
EXACT_SCORE_BASE = 1_000_000
BM25_K1 = 1.2
BM25_B = 0.75
SUMMARY_INDEX_CHARACTERS = 800
FIELD_INDEX_CHARACTERS = 240
EMBEDDING_CONTEXT_SIZE = 512
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class RuleGraphEmbeddingUnavailable(RuntimeError):
    """The configured local embedding model could not be loaded or executed."""


class RuleGraphTextEmbedder(Protocol):
    @property
    def identity(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray: ...


class OnnxTextEmbedder:
    """Lazy ONNX Runtime adapter for the bundled BGE Base model."""

    def __init__(self, model_name: str, model_path: str | None) -> None:
        self.model_name = model_name
        self.model_path = model_path
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._model_lock = Lock()

    @property
    def identity(self) -> str:
        return self.model_name

    def _loaded_runtime(self):
        with self._model_lock:
            if self._session is not None and self._tokenizer is not None:
                return self._session, self._tokenizer
            if self.model_path is None or not Path(self.model_path).is_dir():
                raise RuleGraphEmbeddingUnavailable(
                    f"本地规则向量模型目录不存在：{self.model_path}"
                )
            model_file = Path(self.model_path) / "onnx" / "model.onnx"
            tokenizer_file = Path(self.model_path) / "tokenizer.json"
            if not model_file.is_file() or not tokenizer_file.is_file():
                raise RuleGraphEmbeddingUnavailable(
                    f"本地规则向量模型文件不完整：{self.model_path}"
                )
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                self._session = ort.InferenceSession(
                    str(model_file),
                    providers=["CPUExecutionProvider"],
                )
                self._input_names = {
                    model_input.name for model_input in self._session.get_inputs()
                }
                self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
                self._tokenizer.enable_truncation(max_length=EMBEDDING_CONTEXT_SIZE)
                self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            except Exception as exc:  # pragma: no cover - provider internals vary
                raise RuleGraphEmbeddingUnavailable("本地规则向量模型加载失败") from exc
            return self._session, self._tokenizer

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        try:
            session, tokenizer = self._loaded_runtime()
            values: list[np.ndarray] = []
            for start in range(0, len(texts), 8):
                encodings = tokenizer.encode_batch(list(texts[start : start + 8]))
                available_inputs = {
                    "input_ids": np.asarray(
                        [encoding.ids for encoding in encodings], dtype=np.int64
                    ),
                    "attention_mask": np.asarray(
                        [encoding.attention_mask for encoding in encodings],
                        dtype=np.int64,
                    ),
                    "token_type_ids": np.asarray(
                        [encoding.type_ids for encoding in encodings], dtype=np.int64
                    ),
                }
                model_inputs = {
                    name: value
                    for name, value in available_inputs.items()
                    if name in self._input_names
                }
                if "input_ids" not in model_inputs:
                    raise RuleGraphEmbeddingUnavailable(
                        "规则向量模型缺少 input_ids 输入"
                    )
                last_hidden_state = np.asarray(
                    session.run(None, model_inputs)[0], dtype=np.float32
                )
                if last_hidden_state.ndim != 3:
                    raise RuleGraphEmbeddingUnavailable(
                        "规则向量模型输出维度不正确"
                    )
                values.extend(last_hidden_state[:, 0, :])
        except RuleGraphEmbeddingUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - provider internals vary
            raise RuleGraphEmbeddingUnavailable("规则文本向量生成失败") from exc
        return _normalized_matrix(values)

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts)

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        instructed = [f"{BGE_QUERY_INSTRUCTION}{text}" for text in texts]
        return self._embed(instructed)


@dataclass(frozen=True)
class HybridSearchHit:
    rule_id: str
    score: int
    exact_match: bool
    bm25_rank: int | None
    vector_rank: int | None


@dataclass(frozen=True)
class _RuleSearchDocument:
    rule: GraphRuleSummary
    text: str
    text_hash: str
    token_counts: Counter[str]
    token_count: int


def _normalized_matrix(values: Iterable[np.ndarray]) -> np.ndarray:
    matrix = np.asarray(list(values), dtype=np.float32)
    if matrix.size == 0:
        return np.empty((0, 0), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms


def _search_text(rule: GraphRuleSummary) -> str:
    fields = [f"规则名称：{rule.name}"]
    for label, values in (
        ("别名", rule.aliases),
        ("条件", rule.conditions),
        ("结论", rule.outcomes),
        ("概念", rule.concepts),
    ):
        if values:
            fields.append(f"{label}：{'；'.join(values)}")
    if rule.summary:
        fields.append(f"摘要：{rule.summary[:SUMMARY_INDEX_CHARACTERS]}")
    return "\n".join(fields)


def _segments(value: str) -> tuple[str, ...]:
    segments = tuple(
        key
        for raw_value in re.split(r"[\s,，、;；/|：:。！？（）()【】\[\]]+", value)
        if (key := normalize_graph_key(raw_value))
    )
    if segments:
        return segments
    key = normalize_graph_key(value)
    return (key,) if key else ()


def _character_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for segment in _segments(value):
        tokens.append(segment)
        if len(segment) == 1:
            continue
        for size in (2, 3):
            if len(segment) < size:
                continue
            tokens.extend(
                segment[index : index + size]
                for index in range(len(segment) - size + 1)
            )
    return tuple(tokens)


def _weighted_tokens(rule: GraphRuleSummary) -> Counter[str]:
    counts: Counter[str] = Counter()
    for weight, character_limit, values in (
        (5, FIELD_INDEX_CHARACTERS, (rule.name,)),
        (4, FIELD_INDEX_CHARACTERS, rule.aliases),
        (3, FIELD_INDEX_CHARACTERS, rule.conditions),
        (3, FIELD_INDEX_CHARACTERS, rule.outcomes),
        (2, FIELD_INDEX_CHARACTERS, rule.concepts),
        (1, SUMMARY_INDEX_CHARACTERS, (rule.summary,)),
    ):
        for value in values:
            counts.update(
                {
                    token: frequency * weight
                    for token, frequency in Counter(
                        _character_tokens(value[:character_limit])
                    ).items()
                }
            )
    return counts


def _document(rule: GraphRuleSummary) -> _RuleSearchDocument:
    text = _search_text(rule)
    token_counts = _weighted_tokens(rule)
    return _RuleSearchDocument(
        rule=rule,
        text=text,
        text_hash=sha256(text.encode("utf-8")).hexdigest(),
        token_counts=token_counts,
        token_count=sum(token_counts.values()),
    )


def _exact_priority(query: str, rule: GraphRuleSummary) -> int:
    query_key = normalize_graph_key(query)
    if not query_key:
        return 0
    if query_key == normalize_graph_key(rule.name):
        return 2
    if any(query_key == normalize_graph_key(alias) for alias in rule.aliases):
        return 1
    return 0


def _bm25_rank(
    query: str,
    documents: tuple[_RuleSearchDocument, ...],
) -> tuple[tuple[str, float], ...]:
    if not documents:
        return ()
    query_tokens = set(_character_tokens(query))
    if not query_tokens:
        return ()
    document_frequency = {
        token: sum(token in document.token_counts for document in documents)
        for token in query_tokens
    }
    average_length = sum(document.token_count for document in documents) / len(documents)
    average_length = average_length or 1
    scored: list[tuple[str, float, str]] = []
    for document in documents:
        score = 0.0
        for token in query_tokens:
            frequency = document.token_counts.get(token, 0)
            if frequency == 0:
                continue
            occurrences = document_frequency[token]
            inverse_frequency = log(
                1 + (len(documents) - occurrences + 0.5) / (occurrences + 0.5)
            )
            normalization = frequency + BM25_K1 * (
                1 - BM25_B + BM25_B * document.token_count / average_length
            )
            score += inverse_frequency * frequency * (BM25_K1 + 1) / normalization
        if score > 0:
            scored.append((document.rule.id, score, document.rule.name))
    scored.sort(key=lambda item: (-item[1], item[2], item[0]))
    return tuple((rule_id, score) for rule_id, score, _ in scored[:BM25_RECALL_LIMIT])


def _vector_rank(
    query_vector: np.ndarray,
    document_matrix: np.ndarray,
    documents: tuple[_RuleSearchDocument, ...],
) -> tuple[tuple[str, float], ...]:
    if not documents or document_matrix.size == 0:
        return ()
    similarities = document_matrix @ query_vector
    ranked = sorted(
        (
            (document.rule.id, float(similarities[index]), document.rule.name)
            for index, document in enumerate(documents)
        ),
        key=lambda item: (-item[1], item[2], item[0]),
    )
    return tuple(
        (rule_id, similarity)
        for rule_id, similarity, _ in ranked[:VECTOR_RECALL_LIMIT]
    )


class RuleGraphHybridSearch:
    """Process-wide incremental vector cache plus per-snapshot BM25/RRF ranking."""

    def __init__(
        self,
        *,
        embedder: RuleGraphTextEmbedder | None = None,
        embedding_enabled: bool | None = None,
        result_limit: int = SEARCH_RESULT_LIMIT,
    ) -> None:
        enabled = (
            rule_graph_embedding_enabled()
            if embedding_enabled is None
            else embedding_enabled
        )
        self.embedder = (
            embedder
            if embedder is not None
            else (
                OnnxTextEmbedder(
                    rule_graph_embedding_model(),
                    rule_graph_embedding_model_path(),
                )
                if enabled
                else None
            )
        )
        self.result_limit = result_limit
        self._embedding_cache: dict[str, tuple[str, np.ndarray]] = {}
        self._embedding_error: RuleGraphEmbeddingUnavailable | None = None
        self._lock = Lock()

    @property
    def embedding_error(self) -> RuleGraphEmbeddingUnavailable | None:
        return self._embedding_error

    async def warm(self, rules: tuple[GraphRuleSummary, ...]) -> None:
        if self.embedder is None:
            return
        await asyncio.to_thread(self._warm_sync, rules)

    def _warm_sync(self, rules: tuple[GraphRuleSummary, ...]) -> None:
        with self._lock:
            documents = tuple(_document(rule) for rule in rules)
            try:
                self._document_embeddings(documents)
            except RuleGraphEmbeddingUnavailable as exc:
                self._embedding_error = exc
                raise

    async def search(
        self,
        queries: tuple[str, ...],
        rules: tuple[GraphRuleSummary, ...],
    ) -> tuple[tuple[HybridSearchHit, ...], ...]:
        return await asyncio.to_thread(self._search_sync, queries, rules)

    def _document_embeddings(
        self,
        documents: tuple[_RuleSearchDocument, ...],
    ) -> np.ndarray:
        if self.embedder is None or self._embedding_error is not None:
            return np.empty((0, 0), dtype=np.float32)
        active_ids = {document.rule.id for document in documents}
        self._embedding_cache = {
            rule_id: value
            for rule_id, value in self._embedding_cache.items()
            if rule_id in active_ids
        }
        changed = [
            document
            for document in documents
            if document.rule.id not in self._embedding_cache
            or self._embedding_cache[document.rule.id][0] != document.text_hash
        ]
        if changed:
            vectors = self.embedder.embed_documents([document.text for document in changed])
            if len(vectors) != len(changed):
                raise RuleGraphEmbeddingUnavailable("规则向量数量与规则文本数量不一致")
            for document, vector in zip(changed, vectors, strict=True):
                self._embedding_cache[document.rule.id] = (document.text_hash, vector)
        if not documents:
            return np.empty((0, 0), dtype=np.float32)
        return np.vstack(
            [self._embedding_cache[document.rule.id][1] for document in documents]
        )

    def _search_sync(
        self,
        queries: tuple[str, ...],
        rules: tuple[GraphRuleSummary, ...],
    ) -> tuple[tuple[HybridSearchHit, ...], ...]:
        with self._lock:
            documents = tuple(_document(rule) for rule in rules)
            document_matrix = np.empty((0, 0), dtype=np.float32)
            query_matrix = np.empty((0, 0), dtype=np.float32)
            if self.embedder is not None and self._embedding_error is None:
                try:
                    document_matrix = self._document_embeddings(documents)
                    query_matrix = self.embedder.embed_queries(queries)
                except RuleGraphEmbeddingUnavailable as exc:
                    self._embedding_error = exc
                    document_matrix = np.empty((0, 0), dtype=np.float32)
                    query_matrix = np.empty((0, 0), dtype=np.float32)

            results: list[tuple[HybridSearchHit, ...]] = []
            rules_by_id = {rule.id: rule for rule in rules}
            for query_index, query in enumerate(queries):
                exact = sorted(
                    (
                        (_exact_priority(query, rule), rule.name, rule.id)
                        for rule in rules
                        if _exact_priority(query, rule) > 0
                    ),
                    key=lambda item: (-item[0], item[1], item[2]),
                )
                bm25 = _bm25_rank(query, documents)
                vector = (
                    _vector_rank(query_matrix[query_index], document_matrix, documents)
                    if query_index < len(query_matrix)
                    else ()
                )
                bm25_ranks = {
                    rule_id: rank for rank, (rule_id, _) in enumerate(bm25, start=1)
                }
                vector_ranks = {
                    rule_id: rank for rank, (rule_id, _) in enumerate(vector, start=1)
                }
                exact_ids = {rule_id for _, _, rule_id in exact}
                fused_ids = (set(bm25_ranks) | set(vector_ranks)) - exact_ids
                fused = sorted(
                    fused_ids,
                    key=lambda rule_id: (
                        -(
                            (
                                1 / (RRF_RANK_CONSTANT + bm25_ranks[rule_id])
                                if rule_id in bm25_ranks
                                else 0
                            )
                            + (
                                1 / (RRF_RANK_CONSTANT + vector_ranks[rule_id])
                                if rule_id in vector_ranks
                                else 0
                            )
                        ),
                        rules_by_id[rule_id].name,
                        rule_id,
                    ),
                )
                hits = [
                    HybridSearchHit(
                        rule_id=rule_id,
                        score=EXACT_SCORE_BASE + priority * 1_000,
                        exact_match=True,
                        bm25_rank=bm25_ranks.get(rule_id),
                        vector_rank=vector_ranks.get(rule_id),
                    )
                    for priority, _, rule_id in exact
                ]
                for rule_id in fused:
                    rrf_score = (
                        (
                            1 / (RRF_RANK_CONSTANT + bm25_ranks[rule_id])
                            if rule_id in bm25_ranks
                            else 0
                        )
                        + (
                            1 / (RRF_RANK_CONSTANT + vector_ranks[rule_id])
                            if rule_id in vector_ranks
                            else 0
                        )
                    )
                    hits.append(
                        HybridSearchHit(
                            rule_id=rule_id,
                            score=round(rrf_score * RRF_SCORE_SCALE),
                            exact_match=False,
                            bm25_rank=bm25_ranks.get(rule_id),
                            vector_rank=vector_ranks.get(rule_id),
                        )
                    )
                results.append(tuple(hits[: self.result_limit]))
            return tuple(results)


rule_graph_hybrid_search = RuleGraphHybridSearch()

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.graph_store import GraphRuleSummary
from app.rule_graph_search import (
    EXACT_SCORE_BASE,
    OnnxTextEmbedder,
    RuleGraphEmbeddingUnavailable,
    RuleGraphHybridSearch,
)


class FakeEmbedder:
    identity = "test/chinese-embedding"

    def __init__(self) -> None:
        self.document_batches: list[list[str]] = []
        self.query_batches: list[list[str]] = []

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        if "印绶用伤食" in text or "印用伤食" in text:
            return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
        if "支动作祸福" in text or "支能作祸福" in text:
            return np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float32)

    def embed_documents(self, texts) -> np.ndarray:
        batch = list(texts)
        self.document_batches.append(batch)
        return np.vstack([self._vector(text) for text in batch])

    def embed_queries(self, texts) -> np.ndarray:
        batch = list(texts)
        self.query_batches.append(batch)
        return np.vstack([self._vector(text) for text in batch])


class FailingEmbedder(FakeEmbedder):
    def embed_documents(self, texts) -> np.ndarray:
        del texts
        raise RuleGraphEmbeddingUnavailable("test model unavailable")


def test_onnx_embedder_loads_local_model_and_uses_cls_pooling(
    monkeypatch,
    tmp_path,
) -> None:
    calls: dict[str, object] = {}

    class FakeModelInput:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeEncoding:
        ids = [101, 102]
        attention_mask = [1, 1]
        type_ids = [0, 0]

    class FakeTokenizer:
        @classmethod
        def from_file(cls, tokenizer_path):
            calls["tokenizer_path"] = tokenizer_path
            return cls()

        def enable_truncation(self, **kwargs):
            calls["truncation"] = kwargs

        def enable_padding(self, **kwargs):
            calls["padding"] = kwargs

        def encode_batch(self, texts):
            calls.setdefault("batches", []).append(list(texts))
            return [FakeEncoding() for _ in texts]

    class FakeSession:
        def __init__(self, model_path, **kwargs) -> None:
            calls["model_path"] = model_path
            calls["options"] = kwargs

        def get_inputs(self):
            return [
                FakeModelInput("input_ids"),
                FakeModelInput("attention_mask"),
                FakeModelInput("token_type_ids"),
            ]

        def run(self, output_names, model_inputs):
            del output_names
            calls.setdefault("model_inputs", []).append(model_inputs)
            batch_size, sequence_length = model_inputs["input_ids"].shape
            output = np.zeros((batch_size, sequence_length, 2), dtype=np.float32)
            output[:, 0, :] = [3.0, 4.0]
            return [output]

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(InferenceSession=FakeSession),
    )
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(Tokenizer=FakeTokenizer))
    model_path = tmp_path / "embedding-model"
    (model_path / "onnx").mkdir(parents=True)
    (model_path / "onnx" / "model.onnx").write_bytes(b"test")
    (model_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    embedder = OnnxTextEmbedder("test/bge-base", str(model_path))

    documents = embedder.embed_documents(("规则正文",))
    queries = embedder.embed_queries(("财透七煞",))

    assert calls["model_path"] == str(model_path / "onnx" / "model.onnx")
    assert calls["options"] == {"providers": ["CPUExecutionProvider"]}
    assert calls["tokenizer_path"] == str(model_path / "tokenizer.json")
    assert calls["truncation"] == {"max_length": 512}
    assert calls["batches"][0] == ["规则正文"]
    assert calls["batches"][1] == ["为这个句子生成表示以用于检索相关文章：财透七煞"]
    assert np.allclose(documents, [[0.6, 0.8]])
    assert np.allclose(queries, [[0.6, 0.8]])


def rule(rule_id: str, name: str, *, aliases: tuple[str, ...] = ()) -> GraphRuleSummary:
    return GraphRuleSummary(
        id=rule_id,
        name=name,
        summary=f"{name}的规则摘要",
        aliases=aliases,
        concepts=(),
        outcomes=(),
    )


@pytest.mark.asyncio
async def test_hybrid_search_pins_exact_name_and_alias_matches() -> None:
    engine = RuleGraphHybridSearch(embedder=FakeEmbedder())
    rules = (
        rule("R-name", "财星得地"),
        rule("R-alias", "财星有根", aliases=("财星得地",)),
        rule("R-other", "官星得地"),
    )

    hits = (await engine.search(("财星得地",), rules))[0]

    assert [hit.rule_id for hit in hits[:2]] == ["R-name", "R-alias"]
    assert all(hit.exact_match for hit in hits[:2])
    assert all(hit.score >= EXACT_SCORE_BASE for hit in hits[:2])


@pytest.mark.asyncio
async def test_chinese_bm25_recalls_non_contiguous_name_variant() -> None:
    engine = RuleGraphHybridSearch(embedding_enabled=False)
    rules = (
        rule("R-target", "印绶用伤食取运"),
        rule("R-other", "交通意外判断"),
    )

    hits = (await engine.search(("印用伤食取运",), rules))[0]

    assert hits[0].rule_id == "R-target"
    assert hits[0].exact_match is False
    assert hits[0].bm25_rank == 1
    assert hits[0].vector_rank is None


@pytest.mark.asyncio
async def test_vector_recall_finds_semantically_similar_rule() -> None:
    engine = RuleGraphHybridSearch(embedder=FakeEmbedder())
    rules = (
        rule("R-target", "支能作祸福"),
        rule("R-other", "五行生克次序"),
    )

    hits = (await engine.search(("支动作祸福条件",), rules))[0]

    assert hits[0].rule_id == "R-target"
    assert hits[0].vector_rank == 1
    assert hits[0].score < EXACT_SCORE_BASE


@pytest.mark.asyncio
async def test_hybrid_search_only_embeds_changed_rule_documents() -> None:
    embedder = FakeEmbedder()
    engine = RuleGraphHybridSearch(embedder=embedder)
    initial = (
        rule("R-one", "印绶用伤食取运"),
        rule("R-two", "五行生克次序"),
    )

    await engine.search(("印用伤食",), initial)
    await engine.search(("印用伤食",), initial)
    await engine.search(("印用伤食",), (*initial, rule("R-three", "支能作祸福")))

    assert [len(batch) for batch in embedder.document_batches] == [2, 1]
    assert [len(batch) for batch in embedder.query_batches] == [1, 1, 1]


@pytest.mark.asyncio
async def test_hybrid_search_falls_back_to_bm25_when_embedding_fails() -> None:
    engine = RuleGraphHybridSearch(embedder=FailingEmbedder())

    hits = (
        await engine.search(
            ("印用伤食取运",),
            (rule("R-target", "印绶用伤食取运"),),
        )
    )[0]

    assert hits[0].rule_id == "R-target"
    assert hits[0].bm25_rank == 1
    assert hits[0].vector_rank is None
    assert isinstance(engine.embedding_error, RuleGraphEmbeddingUnavailable)

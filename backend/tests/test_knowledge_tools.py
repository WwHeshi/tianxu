import json
from uuid import uuid4

import pytest

from app.agent_tools import AgentToolInputError, AgentToolRegistry
from app.knowledge_capability import KnowledgeCapability
from app.knowledge_tools import KnowledgeToolSession
from app.models import KnowledgeDocument


def knowledge_document(text: str, *, title: str = "滴天髓阐微") -> KnowledgeDocument:
    data = text.encode("utf-8")
    return KnowledgeDocument(
        id=uuid4(),
        title=title,
        original_filename=f"{title}.txt",
        encoding="utf-8",
        byte_size=len(data),
        sha256="a" * 64,
        file_data=data,
    )


def test_search_returns_five_contexts_and_read_cursor() -> None:
    text = (
        "卷首\n"
        + "前文说明。" * 260
        + "\n财多身弱，富屋贫人。\n"
        + "中间说明。" * 260
        + "\n再论财多身弱，当察日主根气。\n"
        + "\n补论财多身弱，仍须细察。\n" * 4
        + "后文说明。" * 260
    )
    session = KnowledgeToolSession([knowledge_document(text)])
    registry = AgentToolRegistry(session.agent_tools())

    first_page = registry.dispatch(
        "search_knowledge",
        json.dumps(
            {
                "queries": ["财多身弱"],
                "source_ids": [],
            }
        ),
    ).output

    assert first_page[0]["source_id"] == "D001"
    assert "财多身弱" in first_page[0]["context"]
    assert "line_number" not in first_page[0]
    assert len(first_page) == 5
    assert len(first_page[0]["read_cursor"]) == 12

    read_page = registry.dispatch(
        "read_knowledge",
        json.dumps({"cursor": first_page[0]["read_cursor"]}),
    ).output
    assert "citation_id" not in read_page
    assert "财多身弱" in read_page["content"]
    assert read_page["next_cursor"]
    assert "knowledge_version" not in read_page
    assert "line_start" not in read_page
    assert "line_end" not in read_page


def test_cursors_are_bound_to_the_current_run() -> None:
    session = KnowledgeToolSession([knowledge_document("财多身弱，宜察根气。")])
    registry = AgentToolRegistry(session.agent_tools())

    with pytest.raises(AgentToolInputError, match="游标无效"):
        registry.dispatch("read_knowledge", json.dumps({"cursor": "forged"}))


def test_search_interleaves_sources_instead_of_filling_one_book_first() -> None:
    session = KnowledgeToolSession(
        [
            knowledge_document("格局。" * 20, title="甲书"),
            knowledge_document("格局另论。" * 20, title="乙书"),
        ]
    )
    result = AgentToolRegistry(session.agent_tools()).dispatch(
        "search_knowledge",
        json.dumps({"queries": ["格局"], "source_ids": []}),
    ).output

    assert [hit["source_id"] for hit in result[:2]] == ["D001", "D002"]
    search_tool = session.agent_tools()[0]
    assert search_tool.input_schema["required"] == ["queries", "source_ids"]


def test_catalog_is_compact() -> None:
    session = KnowledgeToolSession(
        [
            knowledge_document("第一本正文", title="子平真诠"),
            knowledge_document("第二本正文", title="滴天髓"),
        ]
    )

    catalog = session.catalog_prompt()
    assert "知识库版本" not in catalog
    assert "D001《子平真诠》" in catalog
    assert "D002《滴天髓》" in catalog
    assert "第一本正文" not in catalog


def test_capability_binds_prompt_tools_and_finalizer() -> None:
    session = KnowledgeToolSession([knowledge_document("财多身弱，宜察根气。")])
    capability = KnowledgeCapability(session)
    registry = AgentToolRegistry(capability.tools())
    search = registry.dispatch(
        "search_knowledge",
        json.dumps(
            {
                "queries": ["财多身弱"],
                "source_ids": [],
            }
        ),
    ).output
    registry.dispatch(
        "read_knowledge",
        json.dumps({"cursor": search[0]["read_cursor"]}),
    )

    assert "D001《滴天髓阐微》" in capability.prompt_section()
    assert "search_knowledge 用于定位书籍原文" in capability.prompt_section()
    assert "read_knowledge 用于阅读命中位置的上下文" in capability.prompt_section()
    assert "limit" not in capability.tools()[0].input_schema["properties"]
    assert "cursor" not in capability.tools()[0].input_schema["properties"]
    result = capability.finalize(json.dumps({"answer": "依据已读取的资料判断。"}))
    assert result.name == "knowledge"
    assert result.metadata == {}
    assert result.artifacts == ()

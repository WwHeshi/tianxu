"""Run-scoped full-text search and cursor reading tools for knowledge TXT files."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import token_urlsafe

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from .agent_tools import AgentTool, AgentToolInputError
from .knowledge import decode_stored_txt
from .models import KnowledgeDocument

SEARCH_TOOL_NAME = "search_knowledge"
READ_TOOL_NAME = "read_knowledge"
MAX_SEARCH_QUERIES = 6
SEARCH_RESULTS_PER_PAGE = 5
MAX_TRACKED_HITS = 500
MAX_HITS_PER_QUERY_SOURCE = 100
SEARCH_CONTEXT_CHARS = 180
READ_PAGE_CHARS = 1800
PAGE_BOUNDARY_LOOKAHEAD = 240


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(min_length=1, max_length=MAX_SEARCH_QUERIES)
    source_ids: list[str] = Field(max_length=50)

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            query = value.strip()
            if not 2 <= len(query) <= 40:
                raise ValueError("每个搜索词必须为 2 至 40 个字符")
            if any(character in query for character in "\r\n\t"):
                raise ValueError("搜索词不能包含换行或制表符")
            if query not in cleaned:
                cleaned.append(query)
        return cleaned

    @field_validator("source_ids")
    @classmethod
    def normalize_source_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().upper() for value in values if value.strip()))


class KnowledgeSearchHit(BaseModel):
    source_id: str
    title: str
    matched_query: str
    context: str
    read_cursor: str


class KnowledgeSearchResult(RootModel[list[KnowledgeSearchHit]]):
    pass


class KnowledgeReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor: str = Field(min_length=1, max_length=128)


class KnowledgeReadResult(BaseModel):
    source_id: str
    title: str
    content: str
    previous_cursor: str | None
    next_cursor: str | None


@dataclass(frozen=True)
class _KnowledgeSource:
    source_id: str
    title: str
    text: str


@dataclass(frozen=True)
class _SearchHit:
    source_id: str
    matched_query: str
    position: int


@dataclass(frozen=True)
class _ReadCursor:
    source_id: str
    start: int


class KnowledgeToolSession:
    """Immutable decoded corpus plus opaque cursors scoped to one Agent run."""

    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        ordered = sorted(documents, key=lambda item: (item.title, str(item.id)))
        sources = []
        for index, document in enumerate(ordered, start=1):
            sources.append(
                _KnowledgeSource(
                    source_id=f"D{index:03d}",
                    title=_single_line(document.title),
                    text=decode_stored_txt(document.file_data, document.encoding),
                )
            )
        self._sources = tuple(sources)
        self._sources_by_id = {source.source_id: source for source in sources}
        self._cursors: dict[str, _ReadCursor] = {}

    @property
    def available(self) -> bool:
        return bool(self._sources)

    def catalog_prompt(self) -> str:
        if not self._sources:
            return "本次没有可用的知识库资料。"
        entries = "\n".join(f"- {source.source_id}《{source.title}》" for source in self._sources)
        return f"可用资料：\n{entries}"

    def agent_tools(self) -> tuple[AgentTool, AgentTool]:
        return self._search_agent_tool(), self._read_agent_tool()

    def _new_cursor(self, state: _ReadCursor) -> str:
        cursor = token_urlsafe(9)
        self._cursors[cursor] = state
        return cursor

    def _source(self, source_id: str) -> _KnowledgeSource:
        source = self._sources_by_id.get(source_id)
        if source is None:
            raise AgentToolInputError(f"知识库资料编号不存在：{source_id}")
        return source

    def _find_hits(
        self,
        queries: list[str],
        source_ids: list[str],
    ) -> tuple[_SearchHit, ...]:
        selected_sources = (
            [self._source(source_id) for source_id in source_ids]
            if source_ids
            else list(self._sources)
        )
        groups: list[list[_SearchHit]] = []
        for query in queries:
            for source in selected_sources:
                group: list[_SearchHit] = []
                start = 0
                while len(group) < MAX_HITS_PER_QUERY_SOURCE:
                    position = source.text.find(query, start)
                    if position < 0:
                        break
                    group.append(
                        _SearchHit(
                            source_id=source.source_id,
                            matched_query=query,
                            position=position,
                        )
                    )
                    start = position + max(1, len(query))
                groups.append(group)

        hits: list[_SearchHit] = []
        seen: set[tuple[str, int]] = set()
        group_index = 0
        while any(group_index < len(group) for group in groups):
            for group in groups:
                if group_index >= len(group):
                    continue
                hit = group[group_index]
                key = (hit.source_id, hit.position)
                if key in seen:
                    continue
                hits.append(hit)
                seen.add(key)
                if len(hits) >= MAX_TRACKED_HITS:
                    return tuple(hits)
            group_index += 1
        return tuple(hits)

    def _search(self, payload: KnowledgeSearchInput) -> KnowledgeSearchResult:
        hits = self._find_hits(payload.queries, payload.source_ids)
        page = hits[:SEARCH_RESULTS_PER_PAGE]

        return KnowledgeSearchResult(
            root=[self._search_hit_result(hit) for hit in page],
        )

    def _search_hit_result(self, hit: _SearchHit) -> KnowledgeSearchHit:
        source = self._source(hit.source_id)
        context_start, context_end = _context_bounds(
            source.text,
            hit.position,
            len(hit.matched_query),
        )
        page_start = _page_start_for_match(source.text, hit.position)
        return KnowledgeSearchHit(
            source_id=source.source_id,
            title=source.title,
            matched_query=hit.matched_query,
            context=source.text[context_start:context_end].strip(),
            read_cursor=self._new_cursor(_ReadCursor(source_id=source.source_id, start=page_start)),
        )

    def _read(self, payload: KnowledgeReadInput) -> KnowledgeReadResult:
        stored = self._cursors.get(payload.cursor)
        if not isinstance(stored, _ReadCursor):
            raise AgentToolInputError("阅读游标无效或不属于本次报告")

        source = self._source(stored.source_id)
        start = min(stored.start, len(source.text))
        end = _page_end(source.text, start)
        content = source.text[start:end]

        previous_cursor = None
        if start > 0:
            previous_cursor = self._new_cursor(
                _ReadCursor(
                    source_id=source.source_id,
                    start=_previous_page_start(source.text, start),
                )
            )
        next_cursor = None
        if end < len(source.text):
            next_cursor = self._new_cursor(_ReadCursor(source_id=source.source_id, start=end))

        return KnowledgeReadResult(
            source_id=source.source_id,
            title=source.title,
            content=content,
            previous_cursor=previous_cursor,
            next_cursor=next_cursor,
        )

    def _search_agent_tool(self) -> AgentTool:
        def execute(payload: BaseModel) -> BaseModel:
            if not isinstance(payload, KnowledgeSearchInput):
                raise TypeError("search_knowledge received an unexpected input model")
            return self._search(payload)

        return AgentTool(
            name=SEARCH_TOOL_NAME,
            description=(
                "在当前管理员知识库的 TXT 原文中进行精确全文搜索。提供 1 至 6 个同义或"
                "相关短语；固定返回最多 5 条命中附近的上下文及其阅读游标。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "description": "需要搜索的 1 至 6 个精确关键词或短语。",
                        "items": {"type": "string", "minLength": 2, "maxLength": 40},
                        "maxItems": MAX_SEARCH_QUERIES,
                    },
                    "source_ids": {
                        "type": "array",
                        "description": "空数组搜索全部资料，否则只搜索指定资料。",
                        "items": {"type": "string"},
                        "maxItems": 50,
                    },
                },
                "required": ["queries", "source_ids"],
                "additionalProperties": False,
            },
            input_model=KnowledgeSearchInput,
            execute=execute,
        )

    def _read_agent_tool(self) -> AgentTool:
        def execute(payload: BaseModel) -> BaseModel:
            if not isinstance(payload, KnowledgeReadInput):
                raise TypeError("read_knowledge received an unexpected input model")
            return self._read(payload)

        return AgentTool(
            name=READ_TOOL_NAME,
            description=(
                "读取 search_knowledge 返回的阅读游标所定位的原文页面，并返回上一页、下一页游标。"
            ),
            input_schema={
                "type": "object",
                "properties": {"cursor": {"type": "string", "minLength": 1}},
                "required": ["cursor"],
                "additionalProperties": False,
            },
            input_model=KnowledgeReadInput,
            execute=execute,
        )


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _context_bounds(text: str, position: int, query_length: int) -> tuple[int, int]:
    start = max(0, position - SEARCH_CONTEXT_CHARS)
    previous_newline = text.rfind("\n", start, position)
    if previous_newline >= 0:
        start = previous_newline + 1
    end = min(len(text), position + query_length + SEARCH_CONTEXT_CHARS)
    next_newline = text.find("\n", position + query_length, end)
    if next_newline >= 0:
        end = next_newline
    return start, end


def _page_start_for_match(text: str, position: int) -> int:
    target = max(0, position - READ_PAGE_CHARS // 3)
    previous_newline = text.rfind("\n", max(0, target - PAGE_BOUNDARY_LOOKAHEAD), target)
    return previous_newline + 1 if previous_newline >= 0 else target


def _page_end(text: str, start: int) -> int:
    target = min(len(text), start + READ_PAGE_CHARS)
    if target >= len(text):
        return len(text)
    next_newline = text.find("\n", target, min(len(text), target + PAGE_BOUNDARY_LOOKAHEAD))
    return next_newline + 1 if next_newline >= 0 else target


def _previous_page_start(text: str, start: int) -> int:
    target = max(0, start - READ_PAGE_CHARS)
    previous_newline = text.rfind("\n", max(0, target - PAGE_BOUNDARY_LOOKAHEAD), target)
    return previous_newline + 1 if previous_newline >= 0 else target

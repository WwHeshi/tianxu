"""Read and validate the pinned local MingLi-Bench dataset."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

DATASET_NAME = "DestinyLinker/MingLi-Bench"
EXPECTED_QUESTION_COUNT = 160
AVAILABLE_YEARS = (2022, 2023, 2024, 2025)
QUICK_QUESTION_NUMBERS = frozenset((1, 41, 81, 121, 151))
ANSWER_MARKERS = ("正确答案", "正確答案", "correct_answer", "has_answer")


class DatasetUnavailableError(RuntimeError):
    """The local benchmark data is missing or failed integrity validation."""


@dataclass(frozen=True)
class EvaluationOption:
    letter: str
    text: str


@dataclass(frozen=True)
class EvaluationQuestion:
    """A model-safe question that deliberately has no answer field."""

    id: str
    question_number: int
    original_number: int
    case_id: str
    benchmark_year: int
    category: str
    birth_info: dict[str, Any]
    question: str
    options: tuple[EvaluationOption, ...]


@dataclass(frozen=True)
class MingLiDataset:
    path: Path
    sha256: str
    questions: tuple[EvaluationQuestion, ...]
    labels: dict[str, str]

    def get_question(self, question_id: str) -> EvaluationQuestion:
        try:
            return next(question for question in self.questions if question.id == question_id)
        except StopIteration as exc:
            raise DatasetUnavailableError(f"评测题目不存在：{question_id}") from exc

    def answer_for(self, question_id: str) -> str:
        try:
            return self.labels[question_id]
        except KeyError as exc:
            raise DatasetUnavailableError(f"评测答案不存在：{question_id}") from exc

    def select_questions(
        self,
        *,
        scope: Literal["quick", "year", "all"],
        benchmark_year: int | None,
    ) -> tuple[EvaluationQuestion, ...]:
        if scope == "quick":
            return tuple(
                question
                for question in self.questions
                if question.question_number in QUICK_QUESTION_NUMBERS
            )
        if scope == "year":
            if benchmark_year not in AVAILABLE_YEARS:
                raise DatasetUnavailableError("单年评测必须选择 2022—2025 年")
            return tuple(
                question
                for question in self.questions
                if question.benchmark_year == benchmark_year
            )
        if scope == "all":
            return self.questions
        raise DatasetUnavailableError("不支持的评测范围")


def _candidate_paths() -> tuple[Path, ...]:
    configured = os.getenv("MINGLI_BENCH_DATA_PATH", "").strip()
    backend_root = Path(__file__).resolve().parents[3]
    workspace_root = Path(__file__).resolve().parents[4]
    candidates = [
        Path(configured) if configured else None,
        Path("/app/evaluation_data/data.json"),
        backend_root / "evaluation_data" / "data.json",
        workspace_root / "external" / "MingLi-Bench" / "data" / "data.json",
    ]
    return tuple(path.resolve() for path in candidates if path is not None)


def resolve_dataset_path() -> Path:
    for path in _candidate_paths():
        if path.is_file():
            return path
    searched = "；".join(str(path) for path in _candidate_paths())
    raise DatasetUnavailableError(f"未找到 MingLi-Bench 数据文件，已检查：{searched}")


def _benchmark_year(question_number: int) -> int:
    return 2022 + (question_number - 1) // 40


def _safe_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetUnavailableError(f"数据集字段 {field} 缺失或为空")
    text = value.strip()
    if any(marker in text for marker in ANSWER_MARKERS):
        raise DatasetUnavailableError(f"无标签题目字段 {field} 含答案标记")
    return text


@lru_cache(maxsize=4)
def _load_dataset_at(path_text: str) -> MingLiDataset:
    path = Path(path_text)
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetUnavailableError("MingLi-Bench data.json 不是有效的 UTF-8 JSON") from exc
    raw_questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(raw_questions, list) or len(raw_questions) != EXPECTED_QUESTION_COUNT:
        raise DatasetUnavailableError(
            f"MingLi-Bench 应包含 {EXPECTED_QUESTION_COUNT} 题"
        )

    questions: list[EvaluationQuestion] = []
    labels: dict[str, str] = {}
    for raw in raw_questions:
        if not isinstance(raw, dict):
            raise DatasetUnavailableError("题目记录必须是 JSON 对象")
        question_id = _safe_text(raw.get("id"), field="id")
        try:
            question_number = int(raw["question_number"])
            original_number = int(raw["original_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DatasetUnavailableError(f"题目 {question_id} 的编号无效") from exc
        answer = raw.get("answer")
        if answer not in {"A", "B", "C", "D"}:
            raise DatasetUnavailableError(f"题目 {question_id} 缺少有效答案")
        if question_id in labels:
            raise DatasetUnavailableError(f"题目 ID 重复：{question_id}")
        raw_options = raw.get("options")
        if not isinstance(raw_options, list) or len(raw_options) != 4:
            raise DatasetUnavailableError(f"题目 {question_id} 必须有四个选项")
        options = tuple(
            EvaluationOption(
                letter=_safe_text(option.get("letter"), field=f"{question_id}.option.letter"),
                text=_safe_text(option.get("text"), field=f"{question_id}.option.text"),
            )
            for option in raw_options
            if isinstance(option, dict)
        )
        if tuple(option.letter for option in options) != ("A", "B", "C", "D"):
            raise DatasetUnavailableError(f"题目 {question_id} 的选项字母无效")
        birth_info = raw.get("birth_info")
        if not isinstance(birth_info, dict):
            raise DatasetUnavailableError(f"题目 {question_id} 缺少出生资料")
        safe_birth = {
            key: value
            for key, value in birth_info.items()
            if key not in {"answer", "has_answer", "correct_answer"}
        }
        questions.append(
            EvaluationQuestion(
                id=question_id,
                question_number=question_number,
                original_number=original_number,
                case_id=_safe_text(raw.get("case_id"), field=f"{question_id}.case_id"),
                benchmark_year=_benchmark_year(question_number),
                category=_safe_text(raw.get("category"), field=f"{question_id}.category"),
                birth_info=safe_birth,
                question=_safe_text(raw.get("question"), field=f"{question_id}.question"),
                options=options,
            )
        )
        labels[question_id] = answer

    questions.sort(key=lambda item: item.question_number)
    return MingLiDataset(
        path=path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        questions=tuple(questions),
        labels=labels,
    )


def load_dataset() -> MingLiDataset:
    return _load_dataset_at(str(resolve_dataset_path()))


def clear_dataset_cache() -> None:
    _load_dataset_at.cache_clear()

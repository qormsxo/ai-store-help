"""우리 대화 타입과 Gemini Content 사이를 옮기고, 포기한 HTTP 오류를 우리 예외로 바꾼다."""

import logging
from collections.abc import Sequence

from google.genai.errors import APIError
from google.genai.types import (
    Candidate,
    Content,
    FunctionCall,
    FunctionDeclaration,
    FunctionResponse,
    GenerateContentResponse,
    Part,
)
from pydantic import JsonValue, TypeAdapter

from app.errors import (
    AppError,
    LLMNotConfiguredError,
    LLMRateLimitedError,
    LLMRequestError,
    LLMUnavailableError,
)
from app.llm.types import ModelTurn, ToolCall, ToolDefinition, ToolResultTurn, Turn, UserTurn

logger = logging.getLogger(__name__)

_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


def to_declaration(tool: ToolDefinition) -> FunctionDeclaration:
    """ToolDefinition을 Gemini 도구 선언으로 옮긴다. 우리 모델 객체는 그대로 보내지 않는다."""
    return FunctionDeclaration(name=tool.name, description=tool.description, parameters_json_schema=tool.parameters)


def to_contents(turns: Sequence[Turn]) -> list[Content]:
    """대화 목록 전체를 Gemini Content 목록으로 바꾼다."""
    contents: list[Content] = []
    for turn in turns:
        contents.append(_to_content(turn))
    return contents


def _to_content(turn: Turn) -> Content:
    """대화 칸 하나를 Gemini Content로 바꾼다. ToolCall.signature는 thought_signature로 들어간다."""
    match turn:
        case UserTurn():
            return Content(role="user", parts=[Part(text=turn.text)])
        case ModelTurn():
            return Content(role="model", parts=_model_parts(turn))
        case ToolResultTurn():
            return Content(role="user", parts=_tool_result_parts(turn))


def _model_parts(turn: ModelTurn) -> list[Part]:
    parts: list[Part] = []
    if turn.text:
        parts.append(Part(text=turn.text))
    for call in turn.tool_calls:
        parts.append(
            Part(
                function_call=FunctionCall(id=call.id, name=call.name, args=call.arguments),
                thought_signature=call.signature,
            )
        )
    return parts


def _tool_result_parts(turn: ToolResultTurn) -> list[Part]:
    # Gemini 형식: function_response는 user 역할로, 호출과 같은 id와 name을 붙여 보낸다.
    parts: list[Part] = []
    for result in turn.results:
        parts.append(
            Part(function_response=FunctionResponse(id=result.call_id, name=result.name, response=result.content))
        )
    return parts


def _candidate_parts(candidate: Candidate) -> Sequence[Part]:
    content = candidate.content
    if content is None or not content.parts:
        return []
    return content.parts


def to_model_turn(response: GenerateContentResponse) -> ModelTurn:
    """Gemini 응답 조각 하나를 ModelTurn으로 바꾼다. 글자와 function_call을 우리 필드로 옮긴다."""
    if not response.candidates:
        feedback = response.prompt_feedback
        reason = feedback.block_reason.value if feedback and feedback.block_reason else "NO_CANDIDATES"
        return ModelTurn(finish_reason=f"PROMPT_{reason}")

    candidate = response.candidates[0]
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in _candidate_parts(candidate):
        if part.text and not part.thought:
            texts.append(part.text)
        function_call = part.function_call
        if function_call is None:
            continue
        tool_calls.append(
            ToolCall(
                id=function_call.id,
                name=function_call.name or "",
                arguments=_ARGUMENTS.validate_python(function_call.args or {}),
                signature=part.thought_signature,
            )
        )
    return ModelTurn(
        text="".join(texts) or None,
        tool_calls=tool_calls,
        finish_reason=candidate.finish_reason.value if candidate.finish_reason else None,
    )


def translate_api_error(exc: APIError) -> AppError:
    """재시도를 포기한 HTTP 오류를 우리 예외로 바꾼다."""
    # 응답 본문에는 API 키가 들어 있지 않다. 상태 코드와 Google의 오류 메시지만 남긴다.
    logger.error("Gemini API error %s %s: %s", exc.code, exc.status, exc.message)
    if exc.code == 429:
        return LLMRateLimitedError("AI 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.")
    # Gemini는 잘못된 API 키를 400 INVALID_ARGUMENT로 돌려준다.
    if exc.code in (401, 403) or "API key" in (exc.message or ""):
        return LLMNotConfiguredError("Gemini API 키가 유효하지 않거나 권한이 없습니다.")
    if exc.code == 404:
        return LLMNotConfiguredError("GEMINI_MODEL에 설정한 모델을 찾을 수 없습니다.")
    if exc.code >= 500:
        return LLMUnavailableError("AI 서비스에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요.")
    return LLMRequestError("AI 요청이 거부되었습니다.")

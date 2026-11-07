"""Provider와 무관한 LLM 대화 데이터 구조.

AgentService는 이 타입만 다룬다. 각 Provider가 자기 SDK 형식으로 변환한다.
"""

from pydantic import BaseModel, ConfigDict, JsonValue

type JsonObject = dict[str, JsonValue]


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    # 인자의 JSON Schema. 인자가 없는 Tool은 None.
    parameters: JsonObject | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Provider가 붙인 호출 id. 결과를 돌려줄 때 같은 id로 짝을 맞춘다.
    id: str | None = None
    name: str
    arguments: JsonObject
    # 다음 요청에 그대로 돌려보내야 하는 Provider의 불투명 값(Gemini thought signature).
    signature: bytes | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str | None
    name: str
    content: JsonObject
    # 실행 기록용. Provider는 보내지 않는다(오류 내용은 content의 error에 들어 있다).
    is_error: bool = False


class UserTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str


class ModelTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str | None = None
    tool_calls: list[ToolCall] = []
    # 응답이 비었을 때 원인을 로그로 남기기 위한 값. 다음 요청에는 보내지 않는다.
    finish_reason: str | None = None


class ToolResultTurn(BaseModel):
    """Tool 실행 결과(Observation). 직전 ModelTurn의 tool_calls와 순서와 개수가 같다."""

    model_config = ConfigDict(frozen=True)

    results: list[ToolResult]


type Turn = UserTurn | ModelTurn | ToolResultTurn

"""Provider와 무관한 LLM 대화 데이터 구조.

AgentService는 이 타입만 다룬다. 각 Provider가 자기 SDK 형식으로 변환한다.
"""

from pydantic import BaseModel, ConfigDict, JsonValue

type JsonObject = dict[str, JsonValue]


class ToolDefinition(BaseModel):
    """LLM에게 도구를 알려 줄 때 쓰는 선언. 호출 전에 넘기고, 실행 결과는 담지 않는다."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    # 인자의 JSON Schema. 인자가 없는 Tool은 None.
    parameters: JsonObject | None = None

class ToolResult(BaseModel):
    """도구 하나를 실행한 직후의 결과. Dispatcher가 만들고 다음 LLM 호출에 실어 보낸다."""

    model_config = ConfigDict(frozen=True)

    call_id: str | None
    name: str
    content: JsonObject
    # 실행 기록용. Provider는 보내지 않는다(오류 내용은 content의 error에 들어 있다).
    is_error: bool = False


class UserTurn(BaseModel):
    """고객 메시지 한 건을 LLM 대화에 넣을 때. 이전 기록과 이번 질문이 이 모델이 된다."""

    model_config = ConfigDict(frozen=True)

    text: str


class ModelTurn(BaseModel):
    """LLM 응답 한 건이 끝났을 때. 글, 도구 호출, 또는 둘 다이며 다음 요청의 대화 기록으로 남는다."""

    model_config = ConfigDict(frozen=True)

    text: str | None = None
    tool_calls: list[ToolCall] = []
    # 응답이 비었을 때 원인을 로그로 남기기 위한 값. 다음 요청에는 보내지 않는다.
    finish_reason: str | None = None

    
class ToolCall(BaseModel):
    """모델이 도구 실행을 요청한 호출 한 건. Provider가 응답에서 만들고 Agent가 실행한다."""

    model_config = ConfigDict(frozen=True)

    # Provider가 붙인 호출 id. 결과를 돌려줄 때 같은 id로 짝을 맞춘다.
    id: str | None = None
    name: str
    arguments: JsonObject
    # 다음 요청에 그대로 돌려보내야 하는 Provider의 불투명 값(Gemini thought signature).
    signature: bytes | None = None



class TextDelta(BaseModel):
    """스트리밍 중에만 쓰는 글자 조각. 화면으로 바로 나가고, 이어 붙인 결과가 그 응답의 ModelTurn.text가 된다."""

    model_config = ConfigDict(frozen=True)

    text: str


class ToolResultTurn(BaseModel):
    """도구 실행이 끝난 뒤 결과를 다음 LLM 호출에 붙일 때."""

    model_config = ConfigDict(frozen=True)

    results: list[ToolResult]


type Turn = UserTurn | ModelTurn | ToolResultTurn

# 스트리밍 응답의 항목. TextDelta가 생성되는 대로 나오고, 마지막에 완성된 ModelTurn이 한 번 나온다.
type StreamItem = TextDelta | ModelTurn

"""Tests for the Cloudflare AI Gateway conversation entity."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import probatio
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components import conversation
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import intent, llm
from homeassistant.helpers.selector import TextSelector


async def test_conversation_entity_is_registered(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
) -> None:
    """Test conversation entity is set up from config entry."""
    state = hass.states.async_entity_ids("conversation")
    assert len(state) >= 1


async def test_conversation_agent_responds(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test the conversation agent can handle a message."""
    # Find our conversation entity
    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    result = await conversation.async_converse(
        hass,
        "Hello",
        conversation_id=None,
        context=None,
        agent_id=agent_id,
    )

    assert result.response.response_type == intent.IntentResponseType.ACTION_DONE
    assert "Hello!" in result.response.speech["plain"]["speech"]


async def test_general_question_with_probatio_tools(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
) -> None:
    """Serialize newer HA tool schemas through the real OpenAI SDK."""
    tool = MagicMock(spec=llm.Tool)
    tool.name = "HassTurnOn"
    tool.description = "Turn on a device"
    tool.parameters = probatio.Schema({probatio.Required("name"): TextSelector({}), probatio.Optional("area"): str})

    def native_serializer(schema):
        # Match newer HA's selector serializer and its Probatio sentinel.
        if isinstance(schema, TextSelector):
            return {"type": "string"}
        return probatio.UNSUPPORTED

    api = MagicMock(spec=llm.APIInstance)
    api.api_prompt = "Use tools for device control; answer general questions directly."
    api.tools = [tool]
    api.custom_serializer = native_serializer
    answer = "Stephen Graham Jones is an American author known for horror fiction."
    requests = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["messages"][-1]["content"] == "Who is Stephen Graham Jones?"
        parameters = payload["tools"][0]["function"]["parameters"]
        assert parameters["type"] == "object"
        assert parameters["properties"] == {"name": {"type": "string"}, "area": {"type": "string"}}
        assert parameters["required"] == ["name"]
        event = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": payload["model"],
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request)) as http_client:
        client = openai.AsyncOpenAI(
            api_key="test-cf-token",
            base_url="https://gateway.ai.cloudflare.com/v1/test-account-id/test-gateway/compat",
            http_client=http_client,
        )
        with (
            patch("homeassistant.helpers.llm.async_get_api", return_value=api),
            patch(
                "custom_components.cloudflare_ai_gateway.entity.CloudflareAIGatewayBaseLLMEntity._get_client",
                return_value=client,
            ),
        ):
            result = await conversation.async_converse(
                hass,
                "Who is Stephen Graham Jones?",
                conversation_id=None,
                context=None,
                agent_id="conversation.test_conversation",
            )

    assert result.response.response_type == intent.IntentResponseType.ACTION_DONE
    assert result.response.speech["plain"]["speech"] == answer
    assert len(requests) == 1


def _mock_openai_error(error_class, status_code, message="error"):
    """Create a mock OpenAI API error."""
    mock_response = AsyncMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {"error": {"message": message}}
    mock_response.headers = {}
    return error_class(
        message=message,
        response=mock_response,
        body={"error": {"message": message}},
    )


async def _converse_error(hass, agent_id):
    """Call async_converse and return the error response."""
    result = await conversation.async_converse(
        hass,
        "Hello",
        conversation_id=None,
        context=None,
        agent_id=agent_id,
    )
    assert result.response.response_type == intent.IntentResponseType.ERROR
    return result.response.speech["plain"]["speech"]


async def test_ha_permission_error_does_not_start_cloudflare_reauth(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
) -> None:
    """Local HA authorization must not be mistaken for a Cloudflare failure."""
    with (
        patch("homeassistant.helpers.llm.async_get_api", side_effect=Unauthorized),
        patch.object(mock_config_entry_with_subentries, "async_start_reauth") as reauth,
        patch("custom_components.cloudflare_ai_gateway.entity.CloudflareAIGatewayBaseLLMEntity._get_client") as client,
    ):
        speech = await _converse_error(hass, "conversation.test_conversation")
    assert "Error preparing LLM API" in speech
    reauth.assert_not_called()
    client.assert_not_called()
    assert not any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())


async def test_conversation_auth_error_triggers_reauth(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that authentication errors trigger reauth flow."""
    mock_create_stream.side_effect = _mock_openai_error(openai.AuthenticationError, 401, "Invalid API token")

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "Authentication failed" in speech

    # Verify reauth flow was started
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_conversation_not_found_error(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that 404 errors give a clear model-not-found message."""
    mock_create_stream.side_effect = _mock_openai_error(openai.NotFoundError, 404, "model not found")

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "not found" in speech
    assert "Check the model name" in speech


async def test_conversation_permission_denied_error(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that 403 errors mention provider API key configuration."""
    mock_create_stream.side_effect = _mock_openai_error(openai.PermissionDeniedError, 403, "permission denied")

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "Permission denied" in speech
    assert "provider API key" in speech


async def test_conversation_bad_request_error(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that 400 errors include the API error message."""
    mock_create_stream.side_effect = _mock_openai_error(openai.BadRequestError, 400, "context length exceeded")

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "Bad request" in speech
    assert "context length exceeded" in speech


async def test_conversation_connection_error(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that connection errors give a clear message."""
    mock_create_stream.side_effect = openai.APIConnectionError(request=AsyncMock())

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "Cannot reach Cloudflare AI Gateway" in speech


async def test_conversation_timeout_error(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that timeout errors give a clear message."""
    mock_create_stream.side_effect = openai.APITimeoutError(request=AsyncMock())

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "Request timed out" in speech


async def test_conversation_rate_limit_error(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that rate limit errors give a clear message."""
    mock_create_stream.side_effect = _mock_openai_error(openai.RateLimitError, 429, "rate limited")

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "Rate limited" in speech


def _make_tool_call_chunk(*, index=0, tool_id=None, name=None, arguments=None, finish_reason=None):
    """Create a mock streaming chunk with tool call data."""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].finish_reason = finish_reason
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.role = None
    chunk.choices[0].delta.content = None
    chunk.usage = None

    if tool_id is not None or name is not None or arguments is not None:
        tc = MagicMock()
        tc.index = index
        tc.id = tool_id
        tc.function = MagicMock()
        tc.function.name = name
        tc.function.arguments = arguments
        chunk.choices[0].delta.tool_calls = [tc]
    else:
        chunk.choices[0].delta.tool_calls = None

    return chunk


async def test_conversation_malformed_tool_call_json(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test that malformed JSON in tool call arguments gives a clear error."""
    mock_create_stream.return_value = mock_create_stream.AsyncStreamHelper(
        [
            _make_tool_call_chunk(tool_id="call_123", name="get_weather", arguments="{invalid json"),
            _make_tool_call_chunk(finish_reason="tool_calls"),
        ]
    )

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    speech = await _converse_error(hass, agent_id)
    assert "invalid tool call arguments" in speech.lower()
    assert "get_weather" in speech


async def test_conversation_control_feature_with_llm_api(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
) -> None:
    """Test that CONTROL feature is set when LLM API is configured."""
    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    state = hass.states.get(agent_id)
    assert state is not None
    assert int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)) & conversation.ConversationEntityFeature.CONTROL


async def test_conversation_no_control_feature_without_llm_api(
    hass: HomeAssistant,
    mock_config_entry_no_llm_api: MockConfigEntry,
    mock_init_component_no_llm_api: None,
) -> None:
    """Test that CONTROL feature is not set when LLM API is not configured."""
    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    state = hass.states.get(agent_id)
    assert state is not None
    assert not (int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)) & conversation.ConversationEntityFeature.CONTROL)


async def test_conversation_tool_call_end_to_end(
    hass: HomeAssistant,
    mock_config_entry_with_subentries: MockConfigEntry,
    mock_init_component_with_subentries: None,
    mock_create_stream: AsyncMock,
) -> None:
    """Test tool calling: model requests tool -> tool result -> final response."""

    call_count = 0

    def create_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: model requests a tool call
            return mock_create_stream.AsyncStreamHelper(
                [
                    _make_tool_call_chunk(tool_id="call_1", name="HassTurnOn", arguments='{"name": "kitchen light"}'),
                    _make_tool_call_chunk(finish_reason="tool_calls"),
                ]
            )
        # Second call: model returns final text response
        return mock_create_stream.AsyncStreamHelper(
            [
                mock_create_stream.make_text_chunk("I turned on the kitchen light!", role="assistant"),
                mock_create_stream.make_text_chunk(None, finish_reason="stop"),
                mock_create_stream.make_usage_chunk(),
            ]
        )

    mock_create_stream.side_effect = create_side_effect

    entity_ids = hass.states.async_entity_ids("conversation")
    agent_id = next(eid for eid in entity_ids if eid != "conversation.home_assistant")

    result = await conversation.async_converse(
        hass,
        "Turn on the kitchen light",
        conversation_id=None,
        context=None,
        agent_id=agent_id,
    )

    assert result.response.response_type == intent.IntentResponseType.ACTION_DONE
    # The model was called at least twice (tool call + final response)
    assert call_count >= 2

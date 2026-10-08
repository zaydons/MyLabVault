"""A stand-in for Amazon Bedrock: answers every request with a tool call holding `reply`."""

import json

import anthropic
import httpx2


class BedrockMock:
    def __init__(self):
        self.reply = {}
        self.stop_reason = "tool_use"
        self.status = 200
        self.requests = []

    def _handle(self, request):
        self.requests.append(json.loads(request.content))
        if self.status != 200:
            return httpx2.Response(self.status, json={"message": "denied"})
        tool = self.requests[-1]["tools"][0]["name"]
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "test",
            "content": [{"type": "tool_use", "id": "t1", "name": tool, "input": self.reply}],
            "stop_reason": self.stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    def install(self, monkeypatch):
        from api.services import ai_parser
        real = anthropic.AsyncAnthropicBedrock
        transport = httpx2.MockTransport(self._handle)
        monkeypatch.setattr(ai_parser.anthropic, "AsyncAnthropicBedrock",
                            lambda **kw: real(max_retries=0, http_client=httpx2.AsyncClient(transport=transport), **kw))

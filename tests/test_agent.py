"""Tests for WikiAgent - LLM Agent with WikiFs tool use."""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.wikifs import WikiFs, WikiFsError
from app.services.agent import WIKI_TOOLS, SYSTEM_PROMPT, WikiAgent
from app.services.llm_client import LLMClient
from tests.conftest import MockCache, MockVectorStore, SAMPLE_PATH_TREE, SAMPLE_CHUNKS


# ---------- Mock LLM Client ----------


class MockLLMClient:
    """Mock LLM client that simulates tool call loops."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        """Initialize with a list of responses to return in order.

        Each response should have:
        - "role": "assistant"
        - "content": optional text content
        - "tool_calls": optional list of tool call dicts
        - "finish_reason": optional string
        """
        self._responses = responses or []
        self._call_index = 0

    @property
    def model(self) -> str:
        return "mock-model"

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        """Return the next canned response."""
        if self._call_index < len(self._responses):
            response = self._responses[self._call_index]
            self._call_index += 1
            return response
        # Default: no more responses, return empty content
        return {
            "role": "assistant",
            "content": "No further response available.",
            "finish_reason": "stop",
        }


# ---------- Fixtures ----------


@pytest.fixture
def wikifs() -> WikiFs:
    """Provide a WikiFs instance with mock dependencies."""
    return WikiFs(
        namespace="test",
        version="v1",
        path_tree=SAMPLE_PATH_TREE,
        vector_store=MockVectorStore(),
        cache=MockCache(),
    )


# ---------- Tool Definition Tests ----------


class TestToolDefinitions:
    """Test that tool definitions are correctly structured."""

    def test_six_tools_defined(self) -> None:
        """Should define exactly 6 tools."""
        assert len(WIKI_TOOLS) == 6

    def test_tool_names(self) -> None:
        """All expected tool names should be present."""
        names = {t["function"]["name"] for t in WIKI_TOOLS}
        expected = {"wiki_ls", "wiki_cat", "wiki_grep", "wiki_find", "wiki_tree", "wiki_head"}
        assert names == expected

    def test_tools_have_required_parameters(self) -> None:
        """Each tool should have valid function calling structure."""
        for tool in WIKI_TOOLS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            params = func["parameters"]
            assert params["type"] == "object"
            assert "properties" in params


# ---------- System Prompt Tests ----------


class TestSystemPrompt:
    """Test system prompt content."""

    def test_system_prompt_not_empty(self) -> None:
        """System prompt should not be empty."""
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_mentions_tools(self) -> None:
        """System prompt should mention the available tools."""
        assert "wiki_tree" in SYSTEM_PROMPT
        assert "wiki_ls" in SYSTEM_PROMPT
        assert "wiki_grep" in SYSTEM_PROMPT
        assert "wiki_cat" in SYSTEM_PROMPT

    def test_system_prompt_has_rules(self) -> None:
        """System prompt should include rules about citations."""
        assert "来源" in SYSTEM_PROMPT


# ---------- Agent Tool Execution Tests ----------


class TestAgentToolExecution:
    """Test that the agent correctly routes tool calls to WikiFs."""

    async def test_execute_wiki_ls(self, wikifs: WikiFs) -> None:
        """wiki_ls tool should call WikiFs.ls()."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_ls", {"path": "wiki/"})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert "index.md" in parsed

    async def test_execute_wiki_cat(self, wikifs: WikiFs) -> None:
        """wiki_cat tool should call WikiFs.cat()."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_cat", {"path": "wiki/index.md"})
        assert "Wiki Index" in result

    async def test_execute_wiki_grep(self, wikifs: WikiFs) -> None:
        """wiki_grep tool should call WikiFs.grep()."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_grep", {"pattern": "OAuth", "path": "wiki/"})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        assert "file" in parsed[0]

    async def test_execute_wiki_find(self, wikifs: WikiFs) -> None:
        """wiki_find tool should call WikiFs.find()."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_find", {"path": "wiki/", "name_pattern": "*oauth*"})
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert any("oauth" in p for p in parsed)

    async def test_execute_wiki_tree(self, wikifs: WikiFs) -> None:
        """wiki_tree tool should call WikiFs.tree()."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_tree", {"path": "wiki/", "depth": 2})
        assert "wiki" in result

    async def test_execute_wiki_head(self, wikifs: WikiFs) -> None:
        """wiki_head tool should call WikiFs.head()."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_head", {"path": "wiki/index.md", "lines": 5})
        assert "Wiki Index" in result

    async def test_execute_unknown_tool(self, wikifs: WikiFs) -> None:
        """Unknown tool should return an error message."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_unknown", {})
        assert "Error" in result

    async def test_execute_tool_handles_wikifs_error(self, wikifs: WikiFs) -> None:
        """Tool should gracefully handle WikiFs errors."""
        agent = WikiAgent(wikifs=wikifs, llm_client=MockLLMClient())
        result = await agent._execute_tool("wiki_cat", {"path": "nonexistent.md"})
        assert "Error" in result


# ---------- Agent Chat Flow Tests ----------


class TestAgentChatFlow:
    """Test the agent's chat loop with mock LLM responses."""

    async def test_direct_answer_no_tools(self, wikifs: WikiFs) -> None:
        """Agent should yield content when LLM responds without tool calls."""
        llm = MockLLMClient(responses=[
            {
                "role": "assistant",
                "content": "这是直接回答。",
                "finish_reason": "stop",
            }
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("你好"):
            events.append(event)

        # Should have content and done events
        content_events = [e for e in events if e["type"] == "content"]
        done_events = [e for e in events if e["type"] == "done"]
        assert len(content_events) > 0
        assert len(done_events) == 1
        assert "直接回答" in "".join(e["text"] for e in content_events)

    async def test_tool_call_then_answer(self, wikifs: WikiFs) -> None:
        """Agent should execute tool calls and then produce a final answer."""
        llm = MockLLMClient(responses=[
            # First response: LLM wants to call wiki_ls
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "wiki_ls",
                            "arguments": json.dumps({"path": "wiki/"}),
                        },
                    }
                ],
                "finish_reason": "tool_calls",
            },
            # Second response: LLM answers based on tool result
            {
                "role": "assistant",
                "content": "知识库包含以下目录：summaries、entities、concepts、synthesis [来源: wiki/]",
                "finish_reason": "stop",
            },
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("知识库有哪些目录？"):
            events.append(event)

        # Should have tool_call, tool_result, content, citations, done
        tool_call_events = [e for e in events if e["type"] == "tool_call"]
        tool_result_events = [e for e in events if e["type"] == "tool_result"]
        content_events = [e for e in events if e["type"] == "content"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(tool_call_events) == 1
        assert tool_call_events[0]["tool"] == "wiki_ls"
        assert len(tool_result_events) == 1
        assert len(content_events) > 0
        assert len(done_events) == 1

    async def test_multi_tool_calls(self, wikifs: WikiFs) -> None:
        """Agent should handle multiple tool calls in a single round."""
        llm = MockLLMClient(responses=[
            # First response: LLM calls two tools
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "wiki_ls",
                            "arguments": json.dumps({"path": "wiki/concepts/"}),
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "wiki_cat",
                            "arguments": json.dumps({"path": "wiki/concepts/oauth.md"}),
                        },
                    },
                ],
                "finish_reason": "tool_calls",
            },
            # Second response: final answer
            {
                "role": "assistant",
                "content": "OAuth 2.0 是一个授权框架 [来源: wiki/concepts/oauth.md]",
                "finish_reason": "stop",
            },
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("OAuth 是什么？"):
            events.append(event)

        tool_call_events = [e for e in events if e["type"] == "tool_call"]
        assert len(tool_call_events) == 2
        assert tool_call_events[0]["tool"] == "wiki_ls"
        assert tool_call_events[1]["tool"] == "wiki_cat"

    async def test_multi_round_tool_calls(self, wikifs: WikiFs) -> None:
        """Agent should handle multiple rounds of tool calls."""
        llm = MockLLMClient(responses=[
            # Round 1: tree first
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "wiki_tree",
                            "arguments": json.dumps({"path": "wiki/", "depth": 2}),
                        },
                    },
                ],
                "finish_reason": "tool_calls",
            },
            # Round 2: then grep
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "wiki_grep",
                            "arguments": json.dumps({"pattern": "OAuth", "path": "wiki/"}),
                        },
                    },
                ],
                "finish_reason": "tool_calls",
            },
            # Round 3: then cat
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_3",
                        "type": "function",
                        "function": {
                            "name": "wiki_cat",
                            "arguments": json.dumps({"path": "wiki/concepts/oauth.md"}),
                        },
                    },
                ],
                "finish_reason": "tool_calls",
            },
            # Final answer
            {
                "role": "assistant",
                "content": "OAuth 2.0 是授权框架，涉及授权请求和令牌交换 [来源: wiki/concepts/oauth.md]",
                "finish_reason": "stop",
            },
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("详细解释 OAuth"):
            events.append(event)

        tool_call_events = [e for e in events if e["type"] == "tool_call"]
        assert len(tool_call_events) == 3
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1


# ---------- Citation Extraction Tests ----------


class TestCitationExtraction:
    """Test citation path extraction from responses."""

    async def test_citations_from_cat_tool(self, wikifs: WikiFs) -> None:
        """wiki_cat should track the accessed path as a citation."""
        llm = MockLLMClient(responses=[
            {
                "role": "assistant",
                "content": "信息如下 [来源: wiki/concepts/oauth.md]",
                "finish_reason": "stop",
            }
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("OAuth"):
            events.append(event)

        citation_events = [e for e in events if e["type"] == "citations"]
        assert len(citation_events) >= 1
        assert "wiki/concepts/oauth.md" in citation_events[0]["paths"]

    async def test_citations_from_text_pattern(self, wikifs: WikiFs) -> None:
        """Citations should be extracted from [来源: ...] patterns in text."""
        llm = MockLLMClient(responses=[
            {
                "role": "assistant",
                "content": "答案见 [来源: wiki/index.md, wiki/summaries/project-overview.md]",
                "finish_reason": "stop",
            }
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("项目概览"):
            events.append(event)

        citation_events = [e for e in events if e["type"] == "citations"]
        assert len(citation_events) >= 1
        paths = citation_events[0]["paths"]
        assert "wiki/index.md" in paths
        assert "wiki/summaries/project-overview.md" in paths


# ---------- Multi-turn History Tests ----------


class TestMultiTurnHistory:
    """Test multi-turn conversation history handling."""

    async def test_history_passed_to_messages(self, wikifs: WikiFs) -> None:
        """Conversation history should be included in the messages sent to LLM."""
        llm = MockLLMClient(responses=[
            {
                "role": "assistant",
                "content": "根据之前的讨论...",
                "finish_reason": "stop",
            }
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        history = [
            {"role": "user", "content": "什么是 OAuth？"},
            {"role": "assistant", "content": "OAuth 是授权框架。"},
        ]

        # Build messages internally to verify
        messages = agent._build_messages("它有哪些流程？", history)

        # Should have system + 2 history + 1 current = 4 messages
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1]["content"] == "什么是 OAuth？"
        assert messages[2]["content"] == "OAuth 是授权框架。"
        assert messages[3]["content"] == "它有哪些流程？"

    async def test_no_history(self, wikifs: WikiFs) -> None:
        """Without history, only system + user message should be present."""
        llm = MockLLMClient(responses=[
            {
                "role": "assistant",
                "content": "回答",
                "finish_reason": "stop",
            }
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        messages = agent._build_messages("你好", None)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"


# ---------- Error Handling Tests ----------


class TestErrorHandling:
    """Test graceful degradation when tools fail."""

    async def test_tool_error_graceful(self, wikifs: WikiFs) -> None:
        """Agent should handle tool errors gracefully and continue."""
        llm = MockLLMClient(responses=[
            # First call: try to read a non-existent file
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "wiki_cat",
                            "arguments": json.dumps({"path": "nonexistent.md"}),
                        },
                    },
                ],
                "finish_reason": "tool_calls",
            },
            # Second call: LLM acknowledges the error
            {
                "role": "assistant",
                "content": "抱歉，知识库中没有找到该文件。",
                "finish_reason": "stop",
            },
        ])
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("读取 nonexistent.md"):
            events.append(event)

        tool_result_events = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_result_events) == 1
        assert "Error" in tool_result_events[0]["result"]

        content_events = [e for e in events if e["type"] == "content"]
        assert len(content_events) > 0
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    async def test_max_rounds_exceeded(self, wikifs: WikiFs) -> None:
        """Agent should force a final answer when max rounds exceeded."""
        # Create responses that always return tool calls (infinite loop scenario)
        infinite_tool_response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_loop",
                    "type": "function",
                    "function": {
                        "name": "wiki_ls",
                        "arguments": json.dumps({"path": "wiki/"}),
                    },
                },
            ],
            "finish_reason": "tool_calls",
        }
        # 10 rounds of tool calls + 1 forced final answer
        responses = [infinite_tool_response] * 10 + [
            {
                "role": "assistant",
                "content": "根据已有信息回答。",
                "finish_reason": "stop",
            }
        ]
        llm = MockLLMClient(responses=responses)
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)

        events = []
        async for event in agent.chat("反复查询"):
            events.append(event)

        # Should eventually produce a done event
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1


# ---------- Message Building Tests ----------


class TestMessageBuilding:
    """Test internal message construction."""

    async def test_build_tools_returns_wiki_tools(self, wikifs: WikiFs) -> None:
        """_build_tools should return the WIKI_TOOLS definitions."""
        llm = MockLLMClient()
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)
        tools = agent._build_tools()
        assert tools is WIKI_TOOLS

    async def test_build_system_prompt(self, wikifs: WikiFs) -> None:
        """_build_system_prompt should return the SYSTEM_PROMPT."""
        llm = MockLLMClient()
        agent = WikiAgent(wikifs=wikifs, llm_client=llm)
        prompt = agent._build_system_prompt()
        assert prompt is SYSTEM_PROMPT

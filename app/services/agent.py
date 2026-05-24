"""LLM Agent service - WikiAgent using WikiFs tools to explore knowledge base.

Implements an OpenAI Function Calling agent that uses WikiFs virtual
filesystem tools (ls, cat, grep, find, tree, head) to explore wiki
knowledge bases and answer user questions with citations.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from app.core.wikifs import WikiFs, WikiFsError
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# ---------- Tool Definitions (OpenAI Function Calling format) ----------

WIKI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "wiki_ls",
            "description": "列出指定目录的内容。用于了解 Wiki 知识库的结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径，如 'wiki/' 或 'wiki/concepts/'",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_cat",
            "description": "读取指定文件的完整内容。用于获取 Wiki 页面的详细信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，如 'wiki/concepts/oauth.md'",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_grep",
            "description": "在指定路径下搜索匹配的内容。用于查找包含特定关键词的文件和段落。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式（支持正则表达式）",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索范围路径，默认 'wiki/'",
                        "default": "wiki/",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_find",
            "description": "按文件名模式查找文件。用于定位特定主题的页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "起始搜索路径",
                    },
                    "name_pattern": {
                        "type": "string",
                        "description": "文件名 glob 模式，如 '*.md' 或 '*oauth*'",
                    },
                },
                "required": ["path", "name_pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_tree",
            "description": "显示目录树结构。用于快速概览知识库的组织方式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "根路径",
                        "default": "wiki/",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "显示深度",
                        "default": 3,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_head",
            "description": "读取文件的前N行。用于快速预览文件内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "读取行数",
                        "default": 20,
                    },
                },
                "required": ["path"],
            },
        },
    },
]

# ---------- System Prompt ----------

SYSTEM_PROMPT = """你是一个知识库助手。你可以通过文件系统工具探索 Wiki 知识库来回答用户的问题。

可用工具：
- wiki_tree: 显示目录结构，了解知识库组织方式
- wiki_ls: 列出目录内容
- wiki_find: 按文件名查找
- wiki_grep: 搜索内容关键词
- wiki_cat: 读取完整页面
- wiki_head: 快速预览文件开头

工作流程：
1. 收到问题后，先用 wiki_tree 或 wiki_ls 了解结构
2. 用 wiki_grep 定位相关内容
3. 用 wiki_cat 读取完整页面获取详细信息
4. 综合所有信息回答用户

规则：
- 只基于 Wiki 中的内容回答，不编造信息
- 回答时必须在末尾标注信息来源路径（格式：[来源: path1, path2]）
- 如果知识库中找不到相关信息，明确告知用户
- 回答要简洁准确，直接解决用户问题
"""

# Maximum number of tool call rounds to prevent infinite loops
MAX_TOOL_ROUNDS = 10


class WikiAgent:
    """LLM Agent - uses WikiFs tools to explore knowledge base and answer questions."""

    def __init__(self, wikifs: WikiFs, llm_client: LLMClient) -> None:
        """Initialize WikiAgent.

        Args:
            wikifs: WikiFs instance for the target namespace/version.
            llm_client: LLM client for API calls.
        """
        self._wikifs = wikifs
        self._llm = llm_client
        self._cited_paths: set[str] = set()

    async def chat(
        self,
        question: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Process a user question, yielding SSE events.

        The agent loop:
        1. Build system prompt + tools + user question (with history)
        2. Send to LLM with tool_choice="auto"
        3. If LLM returns tool_calls: execute tools, feed results back
        4. Repeat until LLM produces a final text answer
        5. Stream the final answer and extract citations

        Args:
            question: The user's question.
            history: Optional multi-turn conversation history.

        Yields:
            SSE event dicts with types:
            - {"type": "tool_call", "tool": ..., "args": ...}
            - {"type": "tool_result", "tool": ..., "result": ...}
            - {"type": "content", "text": ...}
            - {"type": "citations", "paths": [...]}
            - {"type": "done"}
        """
        self._cited_paths = set()

        # Build initial messages
        messages = self._build_messages(question, history)
        tools = self._build_tools()

        # Agent loop: may require multiple rounds of tool calls
        for _round in range(MAX_TOOL_ROUNDS):
            # Call LLM (non-streaming during tool call phase for reliability)
            response = await self._llm.chat_completion(
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

            # If no tool calls, this is the final answer - stream it
            if "tool_calls" not in response or not response["tool_calls"]:
                # Stream the final content
                content = response.get("content") or ""
                if content:
                    # Stream in chunks for realistic SSE feel
                    chunk_size = 4
                    for i in range(0, len(content), chunk_size):
                        yield {"type": "content", "text": content[i : i + chunk_size]}

                # Extract and yield citations
                citations = self._extract_citations(content)
                if citations:
                    yield {"type": "citations", "paths": sorted(citations)}

                yield {"type": "done"}
                return

            # Process tool calls
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.get("content"),
                "tool_calls": response["tool_calls"],
            }
            messages.append(assistant_msg)

            # Execute each tool call
            for tool_call in response["tool_calls"]:
                func_name = tool_call["function"]["name"]
                func_args_str = tool_call["function"]["arguments"]
                tool_call_id = tool_call["id"]

                # Parse arguments
                try:
                    func_args = json.loads(func_args_str)
                except json.JSONDecodeError:
                    func_args = {}

                # Emit tool_call event
                yield {"type": "tool_call", "tool": func_name, "args": func_args}

                # Execute the tool
                result = await self._execute_tool(func_name, func_args)

                # Track cited paths
                self._track_citations(func_name, func_args, result)

                # Emit tool_result event (truncated for SSE)
                result_preview = result[:2000] if len(result) > 2000 else result
                yield {"type": "tool_result", "tool": func_name, "result": result_preview}

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })

        # If we exceeded max rounds, force a final answer
        logger.warning("Agent exceeded %d tool call rounds, forcing final answer", MAX_TOOL_ROUNDS)
        messages.append({
            "role": "user",
            "content": "请根据目前已获取的信息回答问题。如果信息不足，请说明。",
        })

        # One more LLM call without tools
        response = await self._llm.chat_completion(
            messages=messages,
            tools=None,
        )
        content = response.get("content") or ""
        if content:
            yield {"type": "content", "text": content}

        citations = self._extract_citations(content)
        if citations:
            yield {"type": "citations", "paths": sorted(citations)}

        yield {"type": "done"}

    def _build_messages(
        self,
        question: str,
        history: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the message list for the LLM API call.

        Args:
            question: The user's question.
            history: Optional conversation history.

        Returns:
            List of message dicts for the API.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        # Add conversation history
        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Add the current question
        messages.append({"role": "user", "content": question})

        return messages

    def _build_tools(self) -> list[dict[str, Any]]:
        """Build the function calling tools definition."""
        return WIKI_TOOLS

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the agent."""
        return SYSTEM_PROMPT

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call by routing to the appropriate WikiFs method.

        Args:
            name: Tool name (e.g., "wiki_ls", "wiki_cat").
            arguments: Tool arguments dict.

        Returns:
            String result of the tool execution.
        """
        try:
            if name == "wiki_ls":
                result = await self._wikifs.ls(arguments.get("path", "/"))
                return json.dumps(result, ensure_ascii=False)

            elif name == "wiki_cat":
                result = await self._wikifs.cat(arguments.get("path", ""))
                return result

            elif name == "wiki_grep":
                result = await self._wikifs.grep(
                    pattern=arguments.get("pattern", ""),
                    path=arguments.get("path", "/"),
                )
                return json.dumps(result, ensure_ascii=False)

            elif name == "wiki_find":
                result = await self._wikifs.find(
                    path=arguments.get("path", "/"),
                    name_pattern=arguments.get("name_pattern", "*"),
                )
                return json.dumps(result, ensure_ascii=False)

            elif name == "wiki_tree":
                result = await self._wikifs.tree(
                    path=arguments.get("path", "/"),
                    depth=arguments.get("depth", 3),
                )
                return result

            elif name == "wiki_head":
                result = await self._wikifs.head(
                    path=arguments.get("path", ""),
                    lines=arguments.get("lines", 20),
                )
                return result

            else:
                return f"Error: Unknown tool '{name}'"

        except WikiFsError as e:
            logger.warning("WikiFs error in tool %s: %s", name, e)
            return f"Error: {e}"
        except Exception as e:
            logger.error("Unexpected error in tool %s: %s", name, e, exc_info=True)
            return f"Error: 工具执行失败 - {e}"

    def _track_citations(self, tool_name: str, args: dict[str, Any], result: str) -> None:
        """Track file paths used by tools for citation extraction.

        Args:
            tool_name: The tool that was called.
            args: The arguments passed to the tool.
            result: The tool result string.
        """
        if tool_name == "wiki_cat":
            path = args.get("path", "")
            if path and "Error" not in result:
                self._cited_paths.add(path)
        elif tool_name == "wiki_head":
            path = args.get("path", "")
            if path and "Error" not in result:
                self._cited_paths.add(path)
        elif tool_name in ("wiki_grep", "wiki_find"):
            # Parse JSON results to extract file paths
            try:
                items = json.loads(result)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "file" in item:
                            self._cited_paths.add(item["file"])
                        elif isinstance(item, str):
                            self._cited_paths.add(item)
            except json.JSONDecodeError:
                pass

    def _extract_citations(self, content: str) -> set[str]:
        """Extract citation paths from the LLM response content.

        Looks for [来源: path1, path2] patterns in the response and
        combines with tracked paths from tool calls.

        Args:
            content: The LLM's response text.

        Returns:
            Set of cited file paths.
        """
        import re

        citations = set(self._cited_paths)

        # Also extract [来源: ...] patterns from the response
        pattern = r'\[来源[:：]\s*([^\]]+)\]'
        matches = re.findall(pattern, content)
        for match in matches:
            paths = [p.strip() for p in match.split(",")]
            citations.update(p for p in paths if p)

        return citations

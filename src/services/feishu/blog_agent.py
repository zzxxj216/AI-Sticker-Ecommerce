"""Blog Agent — ReAct-style AI agent with tool calling for blog management.

The agent receives user messages, maintains conversation memory, and
decides whether to respond directly or call tools (generate_blog,
schedule, publish, etc.) using an LLM-driven reasoning loop.
"""

import json
from typing import Optional

from src.core.logger import get_logger
from src.services.feishu.agent_memory import AgentMemory
from src.services.feishu.agent_tools import AgentToolExecutor, TOOL_DEFINITIONS

logger = get_logger("agent.blog")

AGENT_SYSTEM_PROMPT = """You are a blog management assistant for "Inkelligent", an AI-generated sticker e-commerce brand on Shopify.

## Your Capabilities
You help users generate, schedule, publish, and manage SEO blog posts about sticker topics.
You have access to tools that can:
- Generate full blog posts (with AI planning, writing, image generation, and Shopify publishing)
- Schedule blog generation for a future time
- Check task status, cancel tasks, and list recent blogs
- Publish/unpublish articles on Shopify
- List articles on Shopify
- View the full content of a specific Shopify article
- Upload a locally generated blog to Shopify without regenerating
- List and load previously generated blogs from local disk
- Revise a previously generated article based on user instructions
- Regenerate specific images from a blog post

## Conversation Rules
1. Respond in the SAME language the user uses (Chinese or English)
2. When the user provides a topic, suggest 3-5 SEO keywords and ask for confirmation before generating
3. If the user mentions a time (e.g. "明天上午10点", "next Monday 9am"), use schedule_task instead of generate_blog
4. When the user says "latest" or "刚才那篇", resolve it to the most recent task/article
5. Keep responses concise and informative
6. Always confirm the user's intent before executing destructive actions (cancel, unpublish)
7. When the user asks to modify/revise an article (e.g. "标题改一下", "第三段加点内容", "修改文章"), use revise_article
8. When the user asks to regenerate or change a specific image (e.g. "第3张图重新生成", "换个背景"), use regenerate_image
9. When the user asks to upload/publish a local article to Shopify (e.g. "上传到Shopify", "发布到草稿箱"), use upload_to_shopify
10. When the user refers to a previously generated article that is NOT in the current task memory (e.g. "刚才生成的", "之前那篇"), FIRST call list_local_blogs to find it, then load_local_blog to load it into session before modifying or uploading
11. After generation completes, remind the user they can request modifications

## Response Format
You MUST respond with a JSON object in one of these two formats:

### Format 1: Call a tool
```json
{"action": "tool_call", "tool": "<tool_name>", "params": {<parameters>}, "thinking": "<brief reasoning>"}
```

### Format 2: Respond to user
```json
{"action": "respond", "message": "<your message to the user>", "thinking": "<brief reasoning>"}
```

## Available Tools
{tools_json}

## Important
- ALWAYS return valid JSON, nothing else
- One action per response
- After a tool returns its result, you'll see it as a "tool" message and must decide the next step
- For generate_blog: the pipeline runs in the background; just confirm it started
- For time expressions: convert to ISO format (e.g. "明天上午10点" -> appropriate ISO datetime)
- For revise_article: pass the user's exact modification request as instructions
- For regenerate_image: identify which image number the user wants changed (1-based)
- For upload_to_shopify: use when article was generated locally but not yet published to Shopify
- For list_local_blogs + load_local_blog: use when current session has no task data but user mentions a past article. Always load before trying to revise/upload
"""


def _build_system_prompt() -> str:
    tools_desc = json.dumps(TOOL_DEFINITIONS, indent=2, ensure_ascii=False)
    return AGENT_SYSTEM_PROMPT.replace("{tools_json}", tools_desc)


def _format_messages_for_llm(history: list[dict]) -> str:
    """Format conversation history into a prompt string for the LLM."""
    lines = []
    for msg in history:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            lines.append(f"[User]: {content}")
        elif role == "assistant":
            lines.append(f"[Assistant]: {content}")
        elif role == "tool":
            tool_name = msg.get("name", "unknown")
            lines.append(f"[Tool Result ({tool_name})]: {content}")
        elif role == "system":
            lines.append(f"[Context]: {content}")
    return "\n\n".join(lines)


class BlogAgent:
    """ReAct agent that processes user messages with tool calling."""

    MAX_STEPS = 5

    def __init__(
        self,
        llm_service,
        memory: AgentMemory,
        tools: AgentToolExecutor,
    ):
        """
        Args:
            llm_service: GeminiService instance for reasoning.
            memory: Shared agent memory.
            tools: Tool executor instance.
        """
        self.llm = llm_service
        self.memory = memory
        self.tools = tools
        self._system_prompt = _build_system_prompt()

    def process(self, user_id: str, chat_id: str, message: str) -> str:
        """Process a user message through the ReAct loop.

        Returns the final text response to send to the user.
        """
        self.memory.add_message(user_id, "user", message)

        for step in range(self.MAX_STEPS):
            history = self.memory.get_history(user_id)
            prompt = _format_messages_for_llm(history)

            try:
                response_data = self.llm.generate_json(
                    prompt=prompt,
                    system=self._system_prompt,
                    max_tokens=2048,
                    temperature=0.3,
                )
            except Exception as e:
                logger.error(f"LLM call failed at step {step}: {e}", exc_info=True)
                fallback = "Sorry, I encountered an error. Please try again."
                self.memory.add_message(user_id, "assistant", fallback)
                return fallback

            action = response_data.get("action", "respond")
            thinking = response_data.get("thinking", "")
            if thinking:
                logger.debug(f"Agent thinking (step {step}): {thinking}")

            if action == "tool_call":
                tool_name = response_data.get("tool", "")
                tool_params = response_data.get("params", {})
                logger.info(f"Tool call: {tool_name}({json.dumps(tool_params, ensure_ascii=False)[:200]})")

                result = self.tools.execute(
                    tool_name, tool_params, user_id, chat_id,
                )

                tool_record = json.dumps({
                    "name": tool_name,
                    "content": result,
                }, ensure_ascii=False)
                self.memory.add_message(user_id, "tool", tool_record)

                continue

            # action == "respond"
            final_message = response_data.get("message", "")
            if not final_message:
                final_message = str(response_data)

            self.memory.add_message(user_id, "assistant", final_message)
            logger.info(f"Agent response: {final_message[:100]}...")
            return final_message

        fallback = "I've been thinking too long. Let me summarize: please try a simpler request."
        self.memory.add_message(user_id, "assistant", fallback)
        return fallback

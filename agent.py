"""MASTERMIND v2 — Core async agent loop. ContextPool v3 integrated."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI
from loguru import logger

from context_manager import ContextPool, count_tokens
from tool_registry import get_registry

LLM_MAX_OUTPUT_TOKENS = 30000
# LLM_MODEL = "gpt://b1gd6m9pgdlccgn3of5i/aliceai-llm-flash/latest"
LLM_MODEL = "deepseek-flash"


def construct_history(prompts_list: list[tuple[str, Optional[str]]]) -> list[dict]:
    """Build base prompt messages from text/file tuples."""
    composed = []
    for prompt_text, prompt_file in prompts_list:
        content = prompt_text
        if prompt_file:
            try:
                fp = Path(prompt_file)
                if not fp.is_absolute():
                    fp = Path(__file__).resolve().parent / fp
                content += fp.read_text(encoding='utf-8')
            except Exception:
                logger.warning(f"File {prompt_file} not loaded")
        content += "\n\n---\n\n"
        composed.append({
            "role": "system",
            "name": "INYSHA",
            "content": content,
        })
    return composed


def validate_message_sequence(messages: list[dict]) -> None:
    """
    Check that every tool message has a preceding assistant message
    with a tool_calls entry that includes its tool_call_id.
    Raises ValueError with details if invalid.
    """
    tool_call_ids = set()
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_call_ids.add(tc["id"])
        elif role == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id not in tool_call_ids:
                prev = messages[i-1] if i > 0 else None
                if prev and prev.get("role") == "assistant" and prev.get("tool_calls"):
                    ids_in_prev = {tc["id"] for tc in prev["tool_calls"]}
                    if tc_id in ids_in_prev:
                        continue
                raise ValueError(
                    f"Orphaned tool message at index {i} with tool_call_id={tc_id}. "
                    f"Preceding message: {prev} (if any)."
                )


class Agent:
    """Async reasoning agent with hot-reload tools, crash recovery, and layered context."""

    def __init__(
        self,
        name: str = "MASTERMIND",
        system_prompt: str = "",
        base_prompts: Optional[list] = None,
        last_memory: Optional[list] = None,
        use_tools: bool = True,
        save_history: bool = True,
#        base_url: str = "https://ai.api.cloud.yandex.net/v1",
	base_url: str = "https://api.deepseek.com"
    ):
        self.name = name
        api_key = os.environ.get(f'DEEPSEEK_API_KEY_{self.name}') or os.environ.get('DEEPSEEK_API_KEY', '')
#        api_key = os.getenv("YANDEXGPT_API_KEY")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._system_prompt = {"role": "system", "name": "Creator", "content": system_prompt}
        self._base_prompts = base_prompts or []
        self._last_memory = last_memory or []
        self._use_tools = use_tools
        self._save_history = save_history
        self._helper_agent: Optional[Agent] = None
        self.messages = ContextPool()
        self._plan = None              # current PromptPlan (instruction planner)
        self._active_groups = None     # tool groups exposed this run (None = all)

        # Tell the context manager how large the static prompt is, so its
        # overflow/compression decision measures the real assembled context.
        self._refresh_base_prompt_tokens()

        # Load last memory into context (from previous compression)
        if self._last_memory:
            self.messages.assign_messages(construct_history(self._last_memory))

        logger.info(f"Agent '{name}' initialized ({LLM_MODEL}), "
                     f"crash recovery: {self.messages.length > 0}")

    def add_helper_agent(self, helper: Agent) -> None:
        self._helper_agent = helper

    # ── instruction planning (deterministic prompt/tool scoping) ──────────
    def _refresh_base_prompt_tokens(self) -> None:
        _static = self._system_prompt.get("content", "") or ""
        for _m in construct_history(self._base_prompts):
            _static += _m.get("content", "") or ""
        self.messages.base_prompt_tokens = count_tokens(_static)

    def set_instruction_plan(self, plan) -> None:
        """Apply a PromptPlan: swap base-prompt fragments and scope tools."""
        from lib import instruction_planner as ip
        self._plan = plan
        self._active_groups = set(plan.tool_groups)
        self._base_prompts = ip.base_prompts_for(plan)
        self._refresh_base_prompt_tokens()
        logger.info(f"Instruction plan: {plan.describe()}")

    def enable_tool_group(self, groups) -> list[str]:
        """Unlock extra tool groups for this run (used by ``enable_tools``)."""
        from lib.instruction_planner import ALL_GROUPS
        if self._active_groups is None:
            self._active_groups = set()
        for g in groups or []:
            g = str(g).strip().lower()
            if g in ALL_GROUPS:
                self._active_groups.add(g)
        return sorted(self._active_groups)

    def _plan_for_run(self, prompt: str) -> None:
        """Deterministically pick fragments/tool groups for this run.

        Uses only cheap signals (goal keywords + the foreground window's guide);
        no LLM call. Falls back to exposing all tools if anything goes wrong.
        """
        try:
            from lib import instruction_planner as ip
            from lib import winapi, learning_db
            hwnd = winapi.get_foreground_window() or None
            title = process = ""
            guide_found = False
            guide_key = ""
            if hwnd:
                try:
                    title = winapi.get_window_text(hwnd) or ""
                    process = winapi.get_process_name(winapi.get_window_pid(hwnd)) or ""
                except Exception:
                    pass
                try:
                    res = learning_db.resolve_guide(hwnd=hwnd)
                    guide_found = bool(res.get("found"))
                    guide_key = res.get("key", "")
                except Exception:
                    pass
            ctx = ip.PlannerContext(
                user_prompt=prompt, hwnd=hwnd, title=title, process=process,
                guide_found=guide_found, guide_key=guide_key,
            )
            self.set_instruction_plan(ip.plan(ctx))
        except Exception as e:
            logger.warning(f"instruction planning failed; exposing all tools: {e}")
            self._plan = None
            self._active_groups = None

    @property
    def registry(self):
        """Get the current global tool registry (always up‑to‑date)."""
        return get_registry()

    async def _execute_single_tool(self, tool_call, user_request: str = "") -> dict:
        """Execute one tool call and return the result message."""
        func_name = tool_call.function.name
        registry = get_registry()   # always live
        logger.debug(f"_execute_single_tool: '{func_name}' | registry v{registry._version} | tools: {len(registry._tools)}")
        if func_name not in registry._tools:
            logger.error(f"_execute_single_tool: '{func_name}' NOT in registry._tools! Available: {sorted(registry._tools.keys())}")
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error for {func_name}: {e}")
            return {
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": func_name,
                "content": f"Error: invalid JSON arguments — {e}",
            }

        logger.info(f"Tool: {func_name}({json.dumps(args, ensure_ascii=False)[:200]})")
        tool_timeout = registry.get_timeout(func_name)
        try:
            result = await asyncio.wait_for(
                registry.call_tool(func_name, **args),
                timeout=tool_timeout,  # per-tool timeout (default 120s)
            )
        except asyncio.TimeoutError:
            logger.error(f"Tool '{func_name}' timed out after {tool_timeout:.0f}s")
            return {
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": func_name,
                "content": f"Error: tool '{func_name}' timed out (>{tool_timeout:.0f}s). "
                           f"Try narrowing the search scope.",
            }

        return {
            "tool_call_id": tool_call.id,
            "role": "tool",
            "name": func_name,
            "content": result,
        }

    async def _execute_tools_parallel(self, tool_calls, user_request: str = "") -> list[dict]:
        """Execute multiple tool calls concurrently."""
        tasks = [self._execute_single_tool(tc, user_request) for tc in tool_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                output.append({
                    "tool_call_id": tool_calls[i].id,
                    "role": "tool",
                    "name": tool_calls[i].function.name,
                    "content": f"Error: {result}",
                })
            else:
                output.append(result)
        return output

    def _build_messages_for_llm(self) -> list[dict]:
        """Build full message list with all memory layers.

        Order (DeepSeek cache-optimized):
          system → base_prompts → persistent → compressed → masked → sliding window

        Critical: every assistant message MUST have non-empty content OR tool_calls.
        """
        self.messages.log_state("STATE AT build_messages_for_llm")
        messages: list[dict] = []

        # Layer 0: System prompt (static → prefix-cached by DeepSeek)
        messages.append(self._system_prompt)

        # Layer 1: Base prompts (tool definitions, guidelines — static)
        if self._base_prompts:
            messages.extend(construct_history(self._base_prompts))

        # Layer 2: Persistent memory (key facts across sessions)
        persistent = self.messages._persistent.as_context_string()
        if persistent:
            messages.append({
                "role": "user",
                "name": "MASTERMIND",
                "content": persistent,
            })

        # Layer 3: Compressed history (structured summaries from _compressed_dicts)
        logger.debug(f"Adding {len(self.messages._compressed_dicts)} compressed entries to context")
        for comp_dict in self.messages._compressed_dicts:
            # Build a single assistant message from the structured sections
            content_parts = []
            if comp_dict.get("facts"):
                content_parts.append(f"**Key Facts:**\n{comp_dict['facts']}")
            if comp_dict.get("tool_results"):
                content_parts.append(f"**Tool Results:**\n{comp_dict['tool_results']}")
            if comp_dict.get("decisions"):
                content_parts.append(f"**Decisions:**\n{comp_dict['decisions']}")
            if not content_parts and comp_dict.get("raw_text"):
                content_parts.append(comp_dict["raw_text"])
            if not content_parts:
                content_parts.append("[No structured summary]")
            content = "\n\n".join(content_parts)
            messages.append({
                "role": "assistant",
                "content": content,
                "time": comp_dict.get("timestamp", ""),
            })

        # Layer 4: Masked observations (old tool outputs replaced with [MASKED: ...])
        for entry in self.messages.masked_entries:
            d = {"role": entry.role, "content": entry.content}
            if entry.tool_name:
                d["name"] = entry.tool_name
            if entry.tool_call_id:
                d["tool_call_id"] = entry.tool_call_id
            if entry.tool_calls:
                d["tool_calls"] = entry.tool_calls
            if entry.reasoning:
                d["reasoning_content"] = entry.reasoning
            # Safety: ensure assistant messages always have content or tool_calls
            if entry.role == "assistant" and not d.get("content") and not d.get("tool_calls"):
                if entry.reasoning:
                    d["content"] = f"[Thought: {entry.reasoning.split(chr(10))[0][:200]}]"
                else:
                    d["content"] = "[No content]"
            messages.append(d)


        # Layer 5: Sliding window – last N entries from the master log.
        for entry in self.messages.sliding_window:
            messages.append(self.messages._entry_to_openai_dict(entry))


        # ---- NORMALISE: make the tool-call sequence API-valid ----
        # Two failure modes must be repaired, otherwise the API returns 400:
        #   1. an assistant `tool_calls` message with no matching tool replies
        #      (e.g. the process was killed mid-tool-call and crash recovery
        #      restored a dangling assistant message);
        #   2. an orphaned `tool` message with no preceding `tool_calls`.
        fixed_messages = []
        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                ids = [tc.get("id") for tc in msg["tool_calls"]]
                # Collect the run of tool messages that immediately follow.
                answered: dict = {}
                j = i + 1
                while j < n and messages[j].get("role") == "tool":
                    answered[messages[j].get("tool_call_id")] = messages[j]
                    j += 1
                kept_calls = [tc for tc in msg["tool_calls"] if tc.get("id") in answered]
                new_msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                if kept_calls:
                    new_msg["tool_calls"] = kept_calls
                if not new_msg.get("content") and not new_msg.get("tool_calls"):
                    new_msg["content"] = "[interrupted before tool call]"
                fixed_messages.append(new_msg)
                # Emit only the tool replies that actually answer this message.
                for tid in ids:
                    if tid in answered:
                        fixed_messages.append(answered[tid])
                i = j
            elif role == "tool":
                # Orphaned tool message – convert to a user message.
                fixed_messages.append({
                    "role": "user",
                    "content": f"[Tool result from {msg.get('name', 'unknown')}]:\n{msg.get('content', '')}",
                })
                i += 1
            else:
                fixed_messages.append(msg)
                i += 1

        # Validate the sequence (now should pass)
        try:
            validate_message_sequence(fixed_messages)
        except ValueError as e:
            logger.error(f"Message sequence validation failed: {e}")
            import json
            with open("invalid_messages.json", "w", encoding='utf-8') as f:
                json.dump(fixed_messages, f, indent=2, default=str)
            raise

        return fixed_messages

    async def llm_request(self, enable_reasoning) -> dict:
        """Make an async LLM API call with layered context."""
        messages = self._build_messages_for_llm()

        ctx_len = sum(len(m.get("content", "") or "") + len(m.get("reasoning_content", "") or "")
                      for m in messages)
        est_tokens = ctx_len // 3
        logger.info(f"LLM request: {len(messages)} msgs, ~{est_tokens} tokens")

        registry = get_registry()   # always live
        tools = registry.get_openai_tools(groups=self._active_groups) if self._use_tools else []
        if tools:
            logger.debug(f"llm_request: passing {len(tools)} tools to API: {[t['function']['name'] for t in tools]}")

        try:
            response = await self._client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=tools or None,
                stream=False,
                max_tokens=LLM_MAX_OUTPUT_TOKENS,
                temperature=1.0,
                # extra_body={"thinking": {"type": "enabled"} if enable_reasoning else {"type": "disabled"}}
            )
        except Exception as e:
            logger.exception(f"LLM API call failed: {e}")
            import json
            with open("failed_messages.json", "w", encoding="utf-8") as f:
                json.dump(messages, f, indent=2, default=str)
            raise

        if response is None:
            raise ValueError("API returned None response")
        if not hasattr(response, "choices") or not response.choices:
            raise ValueError(f"Response missing 'choices': {response}")

        response_dict = response.model_dump()
        if not response_dict.get("choices"):
            logger.error(f"LLM response has no choices: {response_dict}")
            raise ValueError(f"Invalid LLM response: {response_dict}")
        return response_dict

    def _maybe_compress_after_run(self) -> None:
        """Start background compression once a run has produced its final answer.

        Compression is never triggered inside the loop; it only happens here (or
        via the manual ``/compress`` command). ``compress()`` re-checks the token
        threshold and ``start_background_compression`` no-ops if one is running.
        """
        if self._helper_agent and self.messages.overflow:
            logger.info("Run finished with overflow — starting background compression")
            self.messages.start_background_compression(self._helper_agent)

    async def compress_context(self, force: bool = False) -> Optional[str]:
        """Compress the context now (used by the manual ``/compress`` command)."""
        if not self._helper_agent:
            return None
        return await self.messages.compress(self._helper_agent, force=force)

    async def run(self, initial_user_request: str = "", reasoning_callback=None) -> str:
        """
        Main agent loop.
        Returns final response text.
        reasoning_callback: async callable(thought_text) for live reasoning output.
        """
        max_iterations = 50
        iteration = 0
        enable_reasoning = bool(reasoning_callback)

        # Never compress mid-loop. If the previous run left a background
        # compression in flight, wait for it so we append to a stable context.
        await self.messages.await_compression()

        # Deterministically scope the prompt fragments and tools for this run.
        if self._use_tools and initial_user_request:
            self._plan_for_run(initial_user_request)

        # Add user message to context
        if initial_user_request:
            self.messages.append({
                "role": "user",
                "name": "User",
                "content": initial_user_request,
            }, save=True)

        while iteration < max_iterations:
            iteration += 1

            # Check for new tools (hot-reload) – always use live registry
            registry = get_registry()
            if registry.version > 0:
                logger.debug(f"Registry v{registry.version}, {len(registry.tool_names)} tools ready")

            try:
                response = await self.llm_request(enable_reasoning)
            except Exception as e:
                logger.exception(f"LLM request failed at iteration {iteration}")
                self.messages.log_state("FAILED LLM REQUEST")
                import json
                with open("failed_context.json", "w", encoding='utf-8') as f:
                    json.dump([self.messages._entry_to_dict(e) for e in self.messages._all_entries], f, indent=2,
                              default=str)
                raise

            choice = response["choices"][0]
            message = choice["message"]

            # Extract reasoning if present
            reasoning = message.get("reasoning_content", "")
            if reasoning and reasoning_callback:
                await reasoning_callback(reasoning)

            has_tool_calls = bool(message.get("tool_calls"))
            logger.debug(f"run: has_tool_calls={has_tool_calls}, message keys={list(message.keys())}")
            logger.debug("run: about to call append_assistant_message")
            self.messages.append_assistant_message(message, self._save_history, has_tool_calls)

            if has_tool_calls:
                tool_calls_raw = message["tool_calls"]
                # Convert dicts to objects for compatibility
                tool_call_objects = []
                for tc in tool_calls_raw:
                    class ToolCallObj:
                        pass
                    obj = ToolCallObj()
                    obj.id = tc["id"]
                    obj.function = type('fn', (), {})()
                    obj.function.name = tc["function"]["name"]
                    obj.function.arguments = tc["function"]["arguments"]
                    tool_call_objects.append(obj)

                # Execute tools in parallel
                tool_results = await self._execute_tools_parallel(
                    tool_call_objects, initial_user_request
                )

                for result in tool_results:
                    self.messages.append(result, save=True)
            else:
                # Final response — no tool calls
                content = message.get("content", "")
                logger.info(f"Agent finished in {iteration} iterations, "
                           f"context: {self.messages.length} msgs, "
                           f"~{self.messages.get_context_length()} tokens")
                self._maybe_compress_after_run()
                return content

        logger.warning(f"Max iterations ({max_iterations}) reached")
        self._maybe_compress_after_run()
        return "Max iterations reached without final response."

    async def run_with_crash_recovery(self, initial_user_request: str = "", reasoning_callback=None) -> str:
        """Run agent with crash recovery."""
        try:
            return await self.run(initial_user_request, reasoning_callback)
        except Exception as e:
            logger.exception(f"Agent crashed: {e}")
            self.messages._emergency_save()
            raise


def NOW() -> str:
    return datetime.now().strftime("%d.%m.%Y, %H:%M")

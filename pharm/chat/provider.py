"""Bounded OpenAI-compatible Chat Completions transport; never expose provider error bodies."""
from __future__ import annotations

import json
import httpx


class ModelError(RuntimeError):
    pass


def response_input(messages):
    result = []
    for message in messages:
        if message.get("_response_items"):
            result.extend(message["_response_items"])
        elif message["role"] == "tool":
            result.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
        elif message.get("tool_calls"):
            if message.get("content"): result.append({"role": "assistant", "content": message["content"]})
            result.extend({"type": "function_call", "call_id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in message["tool_calls"])
        else:
            result.append({"role": message["role"], "content": message["content"]})
    return result


async def complete(config, messages, tools=None, *, short=False, transport=None):
    if not config.get("configured"):
        raise ModelError("请先在模型设置中填写 API 地址、模型名称和密钥")
    limit_key = "max_completion_tokens" if config["base_url"].startswith("https://api.openai.com/") else "max_tokens"
    payload = {"model": config["model"], "messages": messages,
               limit_key: 32 if short else config["max_output_tokens"]}
    if tools:
        payload.update(tools=tools, tool_choice="auto")
    responses = config.get("api_style") == "responses"
    if responses:
        payload = {"model": config["model"], "input": response_input(messages), "store": False,
                   "include": ["reasoning.encrypted_content"], "max_output_tokens": 512 if short else config["max_output_tokens"]}
        if tools:
            payload["tools"] = [{"type": "function", **t["function"], "strict": False} for t in tools]
    endpoint = "/responses" if responses else "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = "Bearer " + config["api_key"]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(65, connect=12), follow_redirects=False, transport=transport) as client:
            async with client.stream("POST", config["base_url"] + endpoint, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    messages_by_code = {401: "密钥无效或没有模型权限", 403: "模型服务拒绝访问", 404: "未找到接口或模型，请核对 API 地址与模型名称", 429: "模型服务限流或额度不足", 400: "模型不接受当前请求格式；请核对接口格式、模型名称及工具调用支持"}
                    raise ModelError(messages_by_code.get(response.status_code, "模型服务暂不可用") + "（HTTP %s）" % response.status_code)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ModelError("模型响应超过大小限制")
                    chunks.append(chunk)
        value = json.loads(b"".join(chunks))
        if responses:
            output = value.get("output", [])
            if not isinstance(output, list): raise ValueError()
            calls = [{"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": item["arguments"]}} for item in output if item.get("type") == "function_call"]
            content = "\n".join(part["text"] for item in output if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text")
            return {"role": "assistant", "content": content[:24000], "tool_calls": calls,
                    "response_items": output, "finish_reason": "length" if value.get("status") == "incomplete" else "stop"}
        choice = value["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict) or (message.get("content") is not None and not isinstance(message["content"], str)):
            raise ValueError()
        return {"role": "assistant", "content": (message.get("content") or "")[:24000],
                "reasoning_content": message.get("reasoning_content") if isinstance(message.get("reasoning_content"), str) else None,
                "tool_calls": message.get("tool_calls") or [], "finish_reason": choice.get("finish_reason")}
    except ModelError:
        raise
    except httpx.TimeoutException:
        raise ModelError("模型响应超时，请重试或减少本轮范围") from None
    except httpx.HTTPError:
        raise ModelError("无法连接模型服务，请检查 API 地址、网络和证书") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("模型服务返回了不兼容的响应格式") from None

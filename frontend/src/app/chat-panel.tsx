"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

type ChatRole = "user" | "assistant";

type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
};

type TokenPayload = {
  token: string;
};

function isTokenPayload(value: unknown): value is TokenPayload {
  return (
    typeof value === "object" &&
    value !== null &&
    "token" in value &&
    typeof value.token === "string"
  );
}

function parseErrorMessage(body: string, status: number): string {
  try {
    const payload: unknown = JSON.parse(body);
    if (
      typeof payload === "object" &&
      payload !== null &&
      "detail" in payload &&
      typeof payload.detail === "string"
    ) {
      return payload.detail;
    }
  } catch {
    // Use the HTTP status message when the response is not JSON.
  }

  return `请求失败（HTTP ${status}）`;
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messageEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isGenerating]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = input.trim();
    const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
    if (!content || isGenerating) {
      return;
    }
    if (!apiBaseUrl) {
      setError("未配置 NEXT_PUBLIC_API_BASE_URL，请设置后端地址。");
      return;
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
    };
    const assistantMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
    };
    const nextMessages = [...messages, userMessage];
    setMessages([...nextMessages, assistantMessage]);
    setInput("");
    setError(null);
    setIsGenerating(true);

    try {
      const response = await fetch(`${apiBaseUrl.replace(/\/+$/, "")}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: nextMessages.map(({ role, content: messageContent }) => ({
            role,
            content: messageContent,
          })),
        }),
      });

      if (!response.ok) {
        throw new Error(parseErrorMessage(await response.text(), response.status));
      }
      if (!response.body) {
        throw new Error("服务器没有返回可读取的响应流。");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let receivedDone = false;

      try {
        while (!receivedDone) {
          const { value, done } = await reader.read();
          if (done) {
            buffer += decoder.decode();
            break;
          }

          buffer += decoder.decode(value, { stream: true });
          buffer = buffer.replace(/\r\n/g, "\n");
          let frameEnd = buffer.indexOf("\n\n");

          while (frameEnd !== -1) {
            const frame = buffer.slice(0, frameEnd);
            buffer = buffer.slice(frameEnd + 2);
            const data = frame
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\n");

            if (data === "[DONE]") {
              receivedDone = true;
              break;
            }
            if (data) {
              const payload: unknown = JSON.parse(data);
              if (!isTokenPayload(payload)) {
                throw new Error("服务器返回了无法识别的数据帧。");
              }
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantMessage.id
                    ? { ...message, content: message.content + payload.token }
                    : message,
                ),
              );
            }

            frameEnd = buffer.indexOf("\n\n");
          }

          if (receivedDone) {
            await reader.cancel();
          }
        }
      } finally {
        reader.releaseLock();
      }

      if (!receivedDone) {
        throw new Error("响应流提前结束，请重试。");
      }
    } catch (caughtError: unknown) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "连接服务时发生未知错误。",
      );
    } finally {
      setIsGenerating(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-8 text-slate-900 sm:px-6 lg:py-12">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-5xl flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl shadow-slate-200/70 lg:min-h-[calc(100vh-6rem)]">
        <header className="flex items-center gap-4 border-b border-slate-200 px-6 py-5 sm:px-8">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-indigo-600 text-lg font-bold text-white shadow-lg shadow-indigo-200">
            AI
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">
              Content Studio
            </p>
            <h1 className="mt-1 text-lg font-semibold tracking-tight sm:text-xl">
              AI 内容运营助手
            </h1>
          </div>
          <div className="ml-auto hidden items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 sm:flex">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            对话工作区
          </div>
        </header>

        <section
          aria-label="对话内容"
          className="flex-1 space-y-6 overflow-y-auto px-4 py-6 sm:px-8 sm:py-8"
        >
          {messages.length === 0 ? (
            <div className="flex min-h-[45vh] flex-col items-center justify-center text-center">
              <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-3xl bg-indigo-50 text-2xl text-indigo-600">
                ✦
              </div>
              <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
                今天想创作什么？
              </h2>
              <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
                和 AI 一起梳理灵感、打磨视频钩子，或规划下一条内容。
              </p>
            </div>
          ) : (
            messages.map((message) => (
              <article
                key={message.id}
                className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[min(85%,42rem)] rounded-2xl px-4 py-3 text-sm leading-7 shadow-sm sm:px-5 ${
                    message.role === "user"
                      ? "rounded-br-md bg-indigo-600 text-white"
                      : "rounded-bl-md border border-slate-200 bg-slate-50 text-slate-800"
                  }`}
                >
                  {message.content ? (
                    <p className="whitespace-pre-wrap">{message.content}</p>
                  ) : isGenerating && message.role === "assistant" ? (
                    <span className="inline-flex items-center gap-2 text-slate-500">
                      <span className="flex gap-1" aria-hidden="true">
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-500 [animation-delay:-0.2s]" />
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-500 [animation-delay:-0.1s]" />
                        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-500" />
                      </span>
                      正在生成
                    </span>
                  ) : null}
                </div>
              </article>
            ))
          )}
          <div ref={messageEndRef} />
        </section>

        <div className="border-t border-slate-200 bg-white px-4 py-4 sm:px-8 sm:py-6">
          {error ? (
            <p
              role="alert"
              className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
            >
              {error}
            </p>
          ) : null}
          {isGenerating ? (
            <p className="mb-3 text-xs font-medium text-indigo-600" role="status">
              正在生成回复…
            </p>
          ) : null}
          <form onSubmit={handleSubmit} className="flex items-end gap-3">
            <label htmlFor="chat-input" className="sr-only">
              输入你的问题
            </label>
            <textarea
              id="chat-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="输入你的想法或问题…"
              rows={2}
              disabled={isGenerating}
              className="min-h-14 flex-1 resize-none rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={isGenerating || !input.trim()}
              className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-2xl bg-indigo-600 px-5 text-sm font-semibold text-white shadow-lg shadow-indigo-200 transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
            >
              {isGenerating ? "生成中" : "发送"}
              {!isGenerating ? <span aria-hidden="true">↑</span> : null}
            </button>
          </form>
          <p className="mt-3 text-center text-xs text-slate-400">
            AI 生成的内容请结合实际情况判断
          </p>
        </div>
      </div>
    </main>
  );
}

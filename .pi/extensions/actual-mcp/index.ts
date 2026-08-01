import fs from "node:fs";
import path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";

function loadDotEnv() {
  try {
    const file = fs.readFileSync(path.join(process.cwd(), ".env"), "utf8");
    for (const line of file.split(/\r?\n/)) {
      const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
      if (match && !process.env[match[1]]) {
        process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, "");
      }
    }
  } catch {
    // Environment variables may be supplied by the shell instead.
  }
}

export default async function (pi: ExtensionAPI) {
  loadDotEnv();
  const url = process.env.ACTUAL_MCP_URL ?? "http://127.0.0.1:3001";
  const token = process.env.ACTUAL_MCP_TOKEN;
  let client: Client | undefined;
  let tools: Array<{ name: string; description?: string }> = [];
  let connectionError = "MCP server is not connected";

  if (token) {
    try {
      client = new Client({ name: "pi-actual-budget", version: "1.0.0" });
      const transport = new SSEClientTransport(new URL(url), {
        eventSourceInit: { fetch: (input: RequestInfo | URL, init?: RequestInit) =>
          fetch(input, { ...init, headers: { ...(init?.headers ?? {}), Authorization: `Bearer ${token}` } }) },
        requestInit: { headers: { Authorization: `Bearer ${token}` } },
      });
      await client.connect(transport);
      const listed = await client.listTools();
      tools = listed.tools.map((tool) => ({ name: tool.name, description: tool.description }));
    } catch (error) {
      connectionError = `Could not connect to Actual MCP at ${url}: ${String(error)}`;
      client = undefined;
    }
  } else {
    connectionError = "ACTUAL_MCP_TOKEN is not set";
  }

  const toolSummary = tools.length
    ? tools.map((tool) => `${tool.name}: ${tool.description ?? "no description"}`).join("\n")
    : connectionError;

  pi.registerTool({
    name: "actual_budget",
    label: "Actual Budget",
    description: `Call a tool on the connected Actual Budget MCP server. Available tools:\n${toolSummary}`,
    parameters: Type.Object({
      tool: Type.String({ description: "Exact MCP tool name from the available tools list" }),
      arguments: Type.Optional(Type.Record(Type.String(), Type.Any(), { description: "Arguments for the MCP tool" })),
    }),
    async execute(_toolCallId, params) {
      if (!client) {
        return { content: [{ type: "text", text: connectionError }], details: {} };
      }
      try {
        const result = await client.callTool({ name: params.tool, arguments: params.arguments ?? {} });
        return { content: result.content as any, details: {} };
      } catch (error) {
        return { content: [{ type: "text", text: `Actual MCP error: ${String(error)}` }], details: {} };
      }
    },
  });

  pi.on("session_shutdown", async () => {
    await client?.close();
  });
}

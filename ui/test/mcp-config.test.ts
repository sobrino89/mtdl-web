// The copyable MCP client examples of the settings page.
import assert from "node:assert/strict";
import { test } from "node:test";

import { claudeCodeCommand, claudeDesktopConfig } from "../src/mcp-config";

test("Claude Code gets the address and the key as header", () => {
  assert.equal(
    claudeCodeCommand("http://nas:8080/mcp", "KEY"),
    'claude mcp add --transport http mtdl-web http://nas:8080/mcp --header "X-Api-Key: KEY"',
  );
});

test("Claude Desktop runs mcp-remote locally; plain http needs its switch", () => {
  const lan = JSON.parse(claudeDesktopConfig("http://192.0.2.10:8080/mcp", "KEY"));
  assert.deepEqual(lan.mcpServers["mtdl-web"], {
    command: "npx",
    args: ["-y", "mcp-remote", "http://192.0.2.10:8080/mcp", "--allow-http", "--header", "X-Api-Key:${MTDL_KEY}"],
    env: { MTDL_KEY: "KEY" },
  });
  const tls = JSON.parse(claudeDesktopConfig("https://mtdl.example/mcp", "KEY"));
  assert.ok(!tls.mcpServers["mtdl-web"].args.includes("--allow-http"));
});

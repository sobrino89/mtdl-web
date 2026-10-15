// How MCP clients reach /mcp (mtdl_web/mcp_server.py), as copyable examples for the settings page. Claude's own
// "custom connector" dialog connects from Anthropic's cloud and cannot reach a server in the LAN; Claude Desktop
// therefore runs a local bridge (mcp-remote) from its config file instead, which uses the own network.

/** Claude Code: HTTP transport with the key as header. */
export function claudeCodeCommand(url: string, key: string): string {
  return `claude mcp add --transport http mtdl-web ${url} --header "X-Api-Key: ${key}"`;
}

/** claude_desktop_config.json: mcp-remote as a local server; plain http needs its --allow-http. The key stays in
 * env, mcp-remote puts it into the header (no spaces in args: some clients split them). */
export function claudeDesktopConfig(url: string, key: string): string {
  const args = ["-y", "mcp-remote", url, ...(url.startsWith("http://") ? ["--allow-http"] : []), "--header", "X-Api-Key:${MTDL_KEY}"];
  return JSON.stringify({ mcpServers: { "mtdl-web": { command: "npx", args, env: { MTDL_KEY: key } } } }, null, 2);
}

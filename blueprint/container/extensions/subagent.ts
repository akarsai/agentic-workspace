/**
 * Subagent extension for the agentic-workspace harness.
 *
 * Spawns child `pi` processes (JSON mode) to run delegated tasks to completion,
 * either synchronously (`subagent`) or fire-and-forget in the background
 * (`subagent_spawn`). Every run gets a "mailbox" directory that acts as the
 * shared communication pipeline between the parent agent and its subagents.
 *
 * Design rules honoured here:
 * - Subagents run to completion with no per-task timeout and no human-in-the-loop.
 * - Subagents are ordinary child processes of the parent `pi`: killing the
 *   parent (session shutdown / Ctrl+D / SIGTERM) kills every running subagent.
 * - Subagents never commit to git; only the parent orchestrator commits.
 * - All scratch state lives under <cwd>/.tmp/subagents/ (gitignored), never /tmp.
 *
 * Mailbox layout (<cwd>/.tmp/subagents/<id>/):
 *   task.md        the delegated task (in)
 *   system-prompt.md  the subagent system prompt (in)
 *   stdout.jsonl   newline-delimited JSON event stream from the child pi
 *   stderr.log     child stderr
 *   session.jsonl  persistent pi session (allows future resume)
 *   status         "running" | "done" | "error" + exit code / stop reason
 *   result.md      final output returned to the parent
 *   meta.json      model, tools, timing, usage, exit code
 */

import { spawn, type ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { AgentToolResult } from "@earendil-works/pi-agent-core";
import type { Message } from "@earendil-works/pi-ai";
import {
	CONFIG_DIR_NAME,
	DynamicBorder,
	type ExtensionAPI,
	type ExtensionContext,
	getAgentDir,
	getMarkdownTheme,
	keyHint,
	parseFrontmatter,
} from "@earendil-works/pi-coding-agent";
import {
	Container,
	Key,
	Markdown,
	matchesKey,
	type SelectItem,
	SelectList,
	Spacer,
	Text,
} from "@earendil-works/pi-tui";
import { Type } from "typebox";

const MAILBOX_ROOT = ".tmp/subagents";
const DEFAULT_MODEL = "deepseek-v4-flash";
const DEFAULT_TOOLS = ["read", "bash", "grep", "find", "ls", "edit", "write"];
const DEFAULT_THINKING = "off";
const PER_TASK_OUTPUT_CAP = 50 * 1024;
const FOLLOWUP_OUTPUT_CAP = 8000;

function envModel(): string {
	return process.env.AGENTIC_SUBAGENT_MODEL || DEFAULT_MODEL;
}

function envTools(): string[] {
	const raw = process.env.AGENTIC_SUBAGENT_TOOLS;
	if (raw) {
		return raw
			.split(",")
			.map((s) => s.trim())
			.filter(Boolean);
	}
	return DEFAULT_TOOLS;
}

function envThinking(): string {
	return process.env.AGENTIC_SUBAGENT_THINKING || DEFAULT_THINKING;
}

// ── Agent definitions ────────────────────────────────────────────────────────

interface AgentConfig {
	name: string;
	description: string;
	tools?: string[];
	model?: string;
	thinking?: string;
	systemPrompt: string;
	source: "user" | "project";
	filePath: string;
}

function loadAgentsFromDir(dir: string, source: "user" | "project"): AgentConfig[] {
	const agents: AgentConfig[] = [];
	if (!fs.existsSync(dir)) return agents;

	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(dir, { withFileTypes: true });
	} catch {
		return agents;
	}

	for (const entry of entries) {
		if (!entry.name.endsWith(".md")) continue;
		if (!entry.isFile() && !entry.isSymbolicLink()) continue;

		const filePath = path.join(dir, entry.name);
		let content: string;
		try {
			content = fs.readFileSync(filePath, "utf-8");
		} catch {
			continue;
		}

		const { frontmatter, body } = parseFrontmatter<Record<string, string>>(content);
		if (!frontmatter.name || !frontmatter.description) continue;

		const tools = frontmatter.tools
			?.split(",")
			.map((t) => t.trim())
			.filter(Boolean);

		agents.push({
			name: frontmatter.name,
			description: frontmatter.description,
			tools: tools && tools.length > 0 ? tools : undefined,
			model: frontmatter.model,
			thinking: frontmatter.thinking,
			systemPrompt: body,
			source,
			filePath,
		});
	}
	return agents;
}

function findNearestProjectAgentsDir(cwd: string): string | null {
	let current = cwd;
	while (true) {
		const candidate = path.join(current, CONFIG_DIR_NAME, "agents");
		try {
			if (fs.statSync(candidate).isDirectory()) return candidate;
		} catch {
			/* keep walking up */
		}
		const parent = path.dirname(current);
		if (parent === current) return null;
		current = parent;
	}
}

function discoverAgents(cwd: string): AgentConfig[] {
	const userDir = path.join(getAgentDir(), "agents");
	const projectDir = findNearestProjectAgentsDir(cwd);

	const map = new Map<string, AgentConfig>();
	for (const a of loadAgentsFromDir(userDir, "user")) map.set(a.name, a);
	if (projectDir) {
		for (const a of loadAgentsFromDir(projectDir, "project")) map.set(a.name, a);
	}
	return Array.from(map.values());
}

// ── Results & display helpers ────────────────────────────────────────────────

interface UsageStats {
	input: number;
	output: number;
	cacheRead: number;
	cacheWrite: number;
	cost: number;
	contextTokens: number;
	turns: number;
}

interface SingleResult {
	agent: string;
	agentSource: string;
	task: string;
	exitCode: number;
	messages: Message[];
	stderr: string;
	usage: UsageStats;
	model?: string;
	stopReason?: string;
	errorMessage?: string;
	mailboxDir: string;
	startedAt: number;
	endedAt?: number;
}

function getFinalOutput(messages: Message[]): string {
	for (let i = messages.length - 1; i >= 0; i--) {
		const msg = messages[i];
		if (msg.role === "assistant") {
			for (const part of msg.content) {
				if (part.type === "text") return part.text;
			}
		}
	}
	return "";
}

function isFailedResult(result: SingleResult): boolean {
	return result.exitCode !== 0 || result.stopReason === "error" || result.stopReason === "aborted";
}

function getResultOutput(result: SingleResult): string {
	if (isFailedResult(result)) {
		return result.errorMessage || result.stderr || getFinalOutput(result.messages) || "(no output)";
	}
	return getFinalOutput(result.messages) || "(no output)";
}

function truncateOutput(output: string, cap: number): string {
	const byteLength = Buffer.byteLength(output, "utf8");
	if (byteLength <= cap) return output;
	let truncated = output.slice(0, cap);
	while (Buffer.byteLength(truncated, "utf8") > cap) truncated = truncated.slice(0, -1);
	return `${truncated}\n\n[Output truncated: ${byteLength - Buffer.byteLength(truncated, "utf8")} bytes omitted.]`;
}

function emptyUsage(): UsageStats {
	return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, cost: 0, contextTokens: 0, turns: 0 };
}

// ── Display helpers (Claude Code-style task cards) ───────────────────────────

const COLLAPSED_ITEM_COUNT = 10;

function formatTokens(count: number): string {
	if (count < 1000) return count.toString();
	if (count < 10000) return `${(count / 1000).toFixed(1)}k`;
	if (count < 1000000) return `${Math.round(count / 1000)}k`;
	return `${(count / 1000000).toFixed(1)}M`;
}

function formatUsageStats(usage: UsageStats, model?: string): string {
	const parts: string[] = [];
	if (usage.turns) parts.push(`${usage.turns} turn${usage.turns > 1 ? "s" : ""}`);
	if (usage.input) parts.push(`↑${formatTokens(usage.input)}`);
	if (usage.output) parts.push(`↓${formatTokens(usage.output)}`);
	if (usage.cacheRead) parts.push(`R${formatTokens(usage.cacheRead)}`);
	if (usage.cacheWrite) parts.push(`W${formatTokens(usage.cacheWrite)}`);
	if (usage.cost) parts.push(`$${usage.cost.toFixed(4)}`);
	if (usage.contextTokens && usage.contextTokens > 0) parts.push(`ctx:${formatTokens(usage.contextTokens)}`);
	if (model) parts.push(model);
	return parts.join(" ");
}

function formatToolCall(
	toolName: string,
	args: Record<string, unknown>,
	themeFg: (color: any, text: string) => string,
): string {
	const shortenPath = (p: string) => {
		const home = os.homedir();
		return p.startsWith(home) ? `~${p.slice(home.length)}` : p;
	};

	switch (toolName) {
		case "bash": {
			const command = (args.command as string) || "...";
			const preview = command.length > 60 ? `${command.slice(0, 60)}...` : command;
			return themeFg("muted", "$ ") + themeFg("toolOutput", preview);
		}
		case "read": {
			const rawPath = (args.file_path || args.path || "...") as string;
			const filePath = shortenPath(rawPath);
			const offset = args.offset as number | undefined;
			const limit = args.limit as number | undefined;
			let text = themeFg("accent", filePath);
			if (offset !== undefined || limit !== undefined) {
				const startLine = offset ?? 1;
				const endLine = limit !== undefined ? startLine + limit - 1 : "";
				text += themeFg("warning", `:${startLine}${endLine ? `-${endLine}` : ""}`);
			}
			return themeFg("muted", "read ") + text;
		}
		case "write": {
			const rawPath = (args.file_path || args.path || "...") as string;
			const filePath = shortenPath(rawPath);
			const content = (args.content || "") as string;
			const lines = content.split("\n").length;
			let text = themeFg("muted", "write ") + themeFg("accent", filePath);
			if (lines > 1) text += themeFg("dim", ` (${lines} lines)`);
			return text;
		}
		case "edit": {
			const rawPath = (args.file_path || args.path || "...") as string;
			return themeFg("muted", "edit ") + themeFg("accent", shortenPath(rawPath));
		}
		case "ls": {
			const rawPath = (args.path || ".") as string;
			return themeFg("muted", "ls ") + themeFg("accent", shortenPath(rawPath));
		}
		case "find": {
			const pattern = (args.pattern || "*") as string;
			const rawPath = (args.path || ".") as string;
			return themeFg("muted", "find ") + themeFg("accent", pattern) + themeFg("dim", ` in ${shortenPath(rawPath)}`);
		}
		case "grep": {
			const pattern = (args.pattern || "") as string;
			const rawPath = (args.path || ".") as string;
			return (
				themeFg("muted", "grep ") +
				themeFg("accent", `/${pattern}/`) +
				themeFg("dim", ` in ${shortenPath(rawPath)}`)
			);
		}
		default: {
			const argsStr = JSON.stringify(args);
			const preview = argsStr.length > 50 ? `${argsStr.slice(0, 50)}...` : argsStr;
			return themeFg("accent", toolName) + themeFg("dim", ` ${preview}`);
		}
	}
}

type DisplayItem = { type: "text"; text: string } | { type: "toolCall"; name: string; args: Record<string, any> };

function getDisplayItems(messages: Message[]): DisplayItem[] {
	const items: DisplayItem[] = [];
	for (const msg of messages) {
		if (msg.role === "assistant") {
			for (const part of msg.content) {
				if (part.type === "text") items.push({ type: "text", text: part.text });
				else if (part.type === "toolCall") items.push({ type: "toolCall", name: part.name, args: part.arguments });
			}
		}
	}
	return items;
}

function isRunning(result: SingleResult): boolean {
	return result.endedAt === undefined;
}

function formatLastActivity(result: SingleResult): string {
	const items = getDisplayItems(result.messages);
	const last = items[items.length - 1];
	if (!last) return "";
	if (last.type === "toolCall") return formatToolCall(last.name, last.args, (_c, t) => t);
	return last.text.split("\n").find((l) => l.trim())?.trim() ?? "";
}

function getPiInvocation(args: string[]): { command: string; args: string[] } {
	const currentScript = process.argv[1];
	const isBunVirtualScript = currentScript?.startsWith("/$bunfs/root/");
	if (currentScript && !isBunVirtualScript && fs.existsSync(currentScript)) {
		return { command: process.execPath, args: [currentScript, ...args] };
	}
	const execName = path.basename(process.execPath).toLowerCase();
	const isGenericRuntime = /^(node|bun)(\.exe)?$/.test(execName);
	if (!isGenericRuntime) {
		return { command: process.execPath, args };
	}
	return { command: "pi", args };
}

// ── Mailbox (shared communication pipeline) ──────────────────────────────────

function writeStatus(dir: string, status: "running" | "done" | "error", extra?: string): void {
	try {
		fs.writeFileSync(path.join(dir, "status"), `${status}${extra ? ` ${extra}` : ""}\n`, "utf-8");
	} catch {
		/* mailbox is best-effort */
	}
}

function finalizeMailbox(result: SingleResult): void {
	const dir = result.mailboxDir;
	const failed = isFailedResult(result);
	const status: "done" | "error" = failed ? "error" : "done";
	const output = getResultOutput(result);
	try {
		writeStatus(dir, status, `exit=${result.exitCode}${result.stopReason ? ` stop=${result.stopReason}` : ""}`);
		fs.writeFileSync(path.join(dir, "result.md"), output, "utf-8");
		fs.writeFileSync(
			path.join(dir, "meta.json"),
			JSON.stringify(
				{
					agent: result.agent,
					model: result.model,
					task: result.task,
					startedAt: new Date(result.startedAt).toISOString(),
					endedAt: result.endedAt ? new Date(result.endedAt).toISOString() : null,
					elapsedMs: (result.endedAt ?? Date.now()) - result.startedAt,
					exitCode: result.exitCode,
					stopReason: result.stopReason ?? null,
					errorMessage: result.errorMessage ?? null,
					usage: result.usage,
				},
				null,
				2,
			),
			"utf-8",
		);
	} catch {
		/* mailbox is best-effort */
	}
}

function buildSubagentSystemPrompt(agent: AgentConfig | undefined, mailboxDir: string): string {
	const parts: string[] = [];
	if (agent?.systemPrompt?.trim()) parts.push(agent.systemPrompt.trim());
	parts.push(
		[
			"You are a subagent running inside the agentic-workspace harness. Work autonomously and complete the task you were given without stopping to ask for help.",
			"Rules:",
			"- Do not run git commit/push/tag or any other git write operation. Only the parent agent commits.",
			`- Keep scratch and temp files under the mailbox directory (${mailboxDir}) or the workspace .tmp directory. Never write to /tmp.`,
			"- Make your final answer your last message; it is returned verbatim to the parent agent as your result.",
		].join("\n"),
	);
	return parts.join("\n\n");
}

// ── Core spawn primitive ─────────────────────────────────────────────────────

interface RunOptions {
	agent: AgentConfig | undefined;
	agentName: string;
	task: string;
	cwd: string;
	mailboxDir: string;
	signal?: AbortSignal;
	onUpdate?: (result: SingleResult) => void;
	trackProc?: (proc: ChildProcess) => void;
	modelOverride?: string;
	toolsOverride?: string[];
}

function runSingleAgent(opts: RunOptions): Promise<SingleResult> {
	const { agent, agentName, task, cwd, mailboxDir, signal, onUpdate, trackProc, modelOverride, toolsOverride } = opts;

	const model = modelOverride || agent?.model || envModel();
	const tools = toolsOverride?.length ? toolsOverride : agent?.tools?.length ? (agent.tools as string[]) : envTools();
	const thinking = agent?.thinking || envThinking();

	const result: SingleResult = {
		agent: agentName,
		agentSource: agent?.source ?? "generic",
		task,
		exitCode: 0,
		messages: [],
		stderr: "",
		usage: emptyUsage(),
		model,
		mailboxDir,
		startedAt: Date.now(),
	};

	fs.mkdirSync(mailboxDir, { recursive: true });
	fs.writeFileSync(path.join(mailboxDir, "task.md"), `# Task\n\n${task}\n`, "utf-8");
	const systemPrompt = buildSubagentSystemPrompt(agent, mailboxDir);
	const systemPromptFile = path.join(mailboxDir, "system-prompt.md");
	fs.writeFileSync(systemPromptFile, systemPrompt, "utf-8");
	writeStatus(mailboxDir, "running", `agent=${agentName}`);

	const args = ["--mode", "json", "-p", "--session", path.join(mailboxDir, "session.jsonl")];
	args.push("--model", model);
	if (tools.length > 0) args.push("--tools", tools.join(","));
	args.push("--thinking", thinking);
	args.push("--append-system-prompt", systemPromptFile);
	args.push(`Task: ${task}`);

	const stdoutStream = fs.createWriteStream(path.join(mailboxDir, "stdout.jsonl"), { flags: "a" });
	const stderrStream = fs.createWriteStream(path.join(mailboxDir, "stderr.log"), { flags: "a" });

	const emitUpdate = () => {
		if (onUpdate) onUpdate(result);
	};

	return new Promise<SingleResult>((resolve) => {
		let proc: ChildProcess;
		try {
			const invocation = getPiInvocation(args);
			proc = spawn(invocation.command, invocation.args, {
				cwd,
				shell: false,
				stdio: ["ignore", "pipe", "pipe"],
			});
		} catch (err) {
			result.exitCode = 1;
			result.errorMessage = `Failed to spawn subagent: ${(err as Error).message}`;
			result.endedAt = Date.now();
			finalizeMailbox(result);
			resolve(result);
			return;
		}

		trackProc?.(proc);

		let buffer = "";
		const processLine = (line: string) => {
			if (!line.trim()) return;
			let event: any;
			try {
				event = JSON.parse(line);
			} catch {
				return;
			}

			if (event.type === "message_end" && event.message) {
				const msg = event.message as Message;
				result.messages.push(msg);
				if (msg.role === "assistant") {
					result.usage.turns++;
					const usage = (msg as any).usage;
					if (usage) {
						result.usage.input += usage.input || 0;
						result.usage.output += usage.output || 0;
						result.usage.cacheRead += usage.cacheRead || 0;
						result.usage.cacheWrite += usage.cacheWrite || 0;
						result.usage.cost += usage.cost?.total || 0;
						result.usage.contextTokens = usage.totalTokens || 0;
					}
					if (!result.model && (msg as any).model) result.model = (msg as any).model;
					if ((msg as any).stopReason) result.stopReason = (msg as any).stopReason;
					if ((msg as any).errorMessage) result.errorMessage = (msg as any).errorMessage;
				}
				emitUpdate();
			}

			if (event.type === "tool_result_end" && event.message) {
				result.messages.push(event.message as Message);
				emitUpdate();
			}
		};

		let killed = false;
		if (signal) {
			const killProc = () => {
				killed = true;
				killGracefully(proc);
			};
			if (signal.aborted) killProc();
			else signal.addEventListener("abort", killProc, { once: true });
		}

		proc.stdout!.on("data", (data) => {
			stdoutStream.write(data);
			buffer += data.toString();
			const lines = buffer.split("\n");
			buffer = lines.pop() || "";
			for (const line of lines) processLine(line);
		});

		proc.stderr!.on("data", (data) => {
			stderrStream.write(data);
			result.stderr += data.toString();
		});

		proc.on("close", (code) => {
			if (buffer.trim()) processLine(buffer);
			stdoutStream.end();
			stderrStream.end();
			result.exitCode = code ?? 0;
			result.endedAt = Date.now();
			if (killed) result.stopReason = "aborted";
			finalizeMailbox(result);
			resolve(result);
		});

		proc.on("error", (err) => {
			stdoutStream.end();
			stderrStream.end();
			result.stderr += `\n${err.message}`;
			result.exitCode = 1;
			result.endedAt = Date.now();
			finalizeMailbox(result);
			resolve(result);
		});
	});
}

function killGracefully(proc: ChildProcess, graceMs = 5000): void {
	if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
	try {
		proc.kill("SIGTERM");
	} catch {
		return;
	}
	setTimeout(() => {
		if (proc.exitCode === null && proc.signalCode === null) {
			try {
				proc.kill("SIGKILL");
			} catch {
				/* already gone */
			}
		}
	}, graceMs);
}

// ── Extension ────────────────────────────────────────────────────────────────

interface BackgroundSub {
	id: string;
	agentName: string;
	task: string;
	mailboxDir: string;
	proc?: ChildProcess;
	startedAt: number;
	status: "running" | "done" | "error";
	lastActivity?: string;
	endedAt?: number;
	exitCode?: number;
}

interface SubagentDetails {
	mode: "single" | "parallel";
	results: SingleResult[];
}

export default function (pi: ExtensionAPI) {
	const background = new Map<string, BackgroundSub>();
	const activeProcs = new Set<ChildProcess>();
	let uiCtx: ExtensionContext | undefined;
	let shuttingDown = false;
	let seq = 0;

	const nextId = (prefix: string) => `${prefix}${Date.now().toString(36)}-${++seq}`;

	const updateSubagentsWidget = () => {
		if (!uiCtx?.hasUI) return;
		const running = Array.from(background.values()).filter((s) => s.status === "running");
		if (running.length === 0) {
			uiCtx.ui.setWidget("subagents", undefined);
			return;
		}
		const th = uiCtx.ui.theme;
		const lines: string[] = [th.fg("accent", th.bold(`⏳ ${running.length} subagent${running.length > 1 ? "s" : ""} running`))];
		for (const s of running) {
			const elapsed = Math.round((Date.now() - s.startedAt) / 1000);
			lines.push(`${th.fg("muted", s.id)} ${th.fg("accent", s.agentName)} (${elapsed}s)`);
			if (s.lastActivity) {
				const act = s.lastActivity.length > 70 ? `${s.lastActivity.slice(0, 70)}…` : s.lastActivity;
				lines.push(`  ${th.fg("dim", act)}`);
			}
		}
		uiCtx.ui.setWidget("subagents", lines, { placement: "belowEditor" });
	};

	const availableAgentNames = (cwd: string) => discoverAgents(cwd).map((a) => a.name).join(", ") || "none";

	const resolveAgent = (name: string | undefined, cwd: string): AgentConfig | undefined => {
		if (!name) return undefined;
		return discoverAgents(cwd).find((a) => a.name === name);
	};

	pi.on("session_shutdown", () => {
		shuttingDown = true;
		for (const proc of [...activeProcs]) killGracefully(proc);
		activeProcs.clear();
		for (const sub of background.values()) {
			if (sub.status === "running") sub.status = "error";
		}
		background.clear();
	});

	pi.on("session_start", async (_event, ctx) => {
		uiCtx = ctx;
		updateSubagentsWidget();
	});

	// ── subagent: synchronous delegation (single or parallel) ────────────────

	const TaskItem = Type.Object({
		agent: Type.Optional(Type.String({ description: "Agent name (from .pi/agents or ~/.pi/agent/agents). Omit for a generic subagent." })),
		task: Type.String({ description: "Task to delegate" }),
		cwd: Type.Optional(Type.String({ description: "Working directory (defaults to the current workspace)" })),
	});

	pi.registerTool({
		name: "subagent",
		label: "Subagent",
		description:
			"Delegate a task to a subagent pi process with an isolated context window, and wait for its final answer. " +
			"Use `agent` + `task` for one task, or `tasks` (array) to run up to 8 tasks with 4 in parallel. " +
			"Agent names are discovered from <workspace>/.pi/agents/*.md and ~/.pi/agent/agents/*.md. " +
			"For long-running background work, prefer subagent_spawn instead.",
		parameters: Type.Object({
			agent: Type.Optional(Type.String({ description: "Agent name for single mode" })),
			task: Type.Optional(Type.String({ description: "Task for single mode" })),
			tasks: Type.Optional(Type.Array(TaskItem, { description: "Array of {agent, task} for parallel execution" })),
			model: Type.Optional(Type.String({ description: "Model override for the subagent(s)" })),
			tools: Type.Optional(Type.String({ description: "Comma-separated tool allowlist override" })),
		}),

		async execute(_toolCallId, params, signal, onUpdate, ctx) {
			const hasTasks = (params.tasks?.length ?? 0) > 0;
			const hasSingle = Boolean(params.task);
			if (Number(hasTasks) + Number(hasSingle) !== 1) {
				return {
					content: [{ type: "text", text: `Provide exactly one mode: {agent, task} or {tasks:[...]}. Available agents: ${availableAgentNames(ctx.cwd)}` }],
					isError: true,
					details: {},
				};
			}

			const toolsOverride = params.tools
				? params.tools.split(",").map((s) => s.trim()).filter(Boolean)
				: undefined;

			const runOne = (agentName: string | undefined, task: string, cwd: string, onUpdateCb?: (r: SingleResult) => void): Promise<SingleResult> => {
				const agent = resolveAgent(agentName, ctx.cwd);
				const mailboxDir = path.join(ctx.cwd, MAILBOX_ROOT, nextId("blk-"));
				return runSingleAgent({
					agent,
					agentName: agent?.name ?? agentName ?? "generic",
					task,
					cwd: cwd ?? ctx.cwd,
					mailboxDir,
					signal,
					onUpdate: onUpdateCb,
					trackProc: (p) => activeProcs.add(p),
					modelOverride: params.model,
					toolsOverride,
				});
			};

			if (hasTasks) {
				const tasks = params.tasks!;
				if (tasks.length > 8) {
					return { content: [{ type: "text", text: `Too many parallel tasks (${tasks.length}); max is 8.` }], isError: true, details: {} };
				}

				const allResults: SingleResult[] = tasks.map((t) => ({
					agent: t.agent ?? "generic",
					agentSource: "generic",
					task: t.task,
					exitCode: -1,
					messages: [],
					stderr: "",
					usage: emptyUsage(),
					mailboxDir: "",
					startedAt: Date.now(),
				}));

				const emitParallelUpdate = () => {
					if (onUpdate) {
						const done = allResults.filter((r) => !isRunning(r)).length;
						const running = allResults.length - done;
						onUpdate({
							content: [{ type: "text", text: `Parallel: ${done}/${allResults.length} done, ${running} running...` }],
							details: { mode: "parallel", results: [...allResults] },
						});
					}
				};
				emitParallelUpdate();

				const results = await mapWithConcurrencyLimit(tasks, 4, (t, i) =>
					runOne(t.agent, t.task, t.cwd ?? ctx.cwd, (r) => {
						allResults[i] = r;
						emitParallelUpdate();
					}),
				);
				const successCount = results.filter((r) => !isFailedResult(r)).length;
				const summaries = results.map((r) => `### [${r.agent}] ${isFailedResult(r) ? "failed" : "completed"}\n\n${truncateOutput(getResultOutput(r), PER_TASK_OUTPUT_CAP)}`);
				return {
					content: [{ type: "text", text: `Parallel: ${successCount}/${results.length} succeeded\n\n${summaries.join("\n\n---\n\n")}` }],
					details: { mode: "parallel", results },
				};
			}

			const result = await runOne(params.agent, params.task!, ctx.cwd, onUpdate
				? (r) => onUpdate({ content: [{ type: "text", text: getFinalOutput(r.messages) || "(running...)" }], details: { mode: "single", results: [r] } })
				: undefined);
			const output = truncateOutput(getResultOutput(result), PER_TASK_OUTPUT_CAP);
			return {
				content: [{ type: "text", text: isFailedResult(result) ? `Subagent failed (${result.stopReason ?? "error"}): ${output}` : output }],
				details: { mode: "single", results: [result] },
				isError: isFailedResult(result),
			};
		},

		renderCall(args, theme, _context) {
			if (args.tasks && args.tasks.length > 0) {
				let text = theme.fg("toolTitle", theme.bold("subagent ")) + theme.fg("accent", `parallel (${args.tasks.length} tasks)`);
				for (const t of args.tasks.slice(0, 3)) {
					const preview = t.task.length > 40 ? `${t.task.slice(0, 40)}...` : t.task;
					text += `\n  ${theme.fg("accent", t.agent ?? "generic")}${theme.fg("dim", ` ${preview}`)}`;
				}
				if (args.tasks.length > 3) text += `\n  ${theme.fg("muted", `... +${args.tasks.length - 3} more`)}`;
				return new Text(text, 0, 0);
			}
			const agentName = args.agent || "generic";
			const preview = args.task ? (args.task.length > 60 ? `${args.task.slice(0, 60)}...` : args.task) : "...";
			let text = theme.fg("toolTitle", theme.bold("subagent ")) + theme.fg("accent", agentName);
			text += `\n  ${theme.fg("dim", preview)}`;
			return new Text(text, 0, 0);
		},

		renderResult(result, { expanded, isPartial }, theme, _context) {
			const details = result.details as SubagentDetails | undefined;
			if (!details || !details.results || details.results.length === 0) {
				const text = result.content[0];
				return new Text(text?.type === "text" ? text.text : "(no output)", 0, 0);
			}

			const mdTheme = getMarkdownTheme();
			const expandHint = () => theme.fg("muted", `(${keyHint("app.tools.expand", "to expand")})`);

			const renderItems = (items: DisplayItem[], limit?: number) => {
				const toShow = limit ? items.slice(-limit) : items;
				const skipped = limit && items.length > limit ? items.length - limit : 0;
				let text = "";
				if (skipped > 0) text += theme.fg("muted", `... ${skipped} earlier items\n`);
				for (const item of toShow) {
					if (item.type === "text") {
						const preview = expanded ? item.text : item.text.split("\n").slice(0, 3).join("\n");
						text += `${theme.fg("toolOutput", preview)}\n`;
					} else {
						text += `${theme.fg("muted", "→ ") + formatToolCall(item.name, item.args, theme.fg.bind(theme))}\n`;
					}
				}
				return text.trimEnd();
			};

			if (details.mode === "single" && details.results.length === 1) {
				const r = details.results[0];
				const running = isRunning(r) || isPartial;
				const isError = !running && isFailedResult(r);
				const icon = running ? theme.fg("warning", "⏳") : isError ? theme.fg("error", "✗") : theme.fg("success", "✓");
				const displayItems = getDisplayItems(r.messages);
				const finalOutput = getFinalOutput(r.messages);

				if (expanded) {
					const container = new Container();
					let header = `${icon} ${theme.fg("toolTitle", theme.bold(r.agent))}${theme.fg("muted", ` (${r.agentSource})`)}`;
					if (isError && r.stopReason) header += ` ${theme.fg("error", `[${r.stopReason}]`)}`;
					container.addChild(new Text(header, 0, 0));
					if (isError && r.errorMessage) container.addChild(new Text(theme.fg("error", `Error: ${r.errorMessage}`), 0, 0));
					container.addChild(new Spacer(1));
					container.addChild(new Text(theme.fg("muted", "─── Task ───"), 0, 0));
					container.addChild(new Text(theme.fg("dim", r.task), 0, 0));
					container.addChild(new Spacer(1));
					container.addChild(new Text(theme.fg("muted", "─── Output ───"), 0, 0));
					if (displayItems.length === 0 && !finalOutput) {
						container.addChild(new Text(theme.fg("muted", running ? "(running...)" : "(no output)"), 0, 0));
					} else {
						for (const item of displayItems) {
							if (item.type === "toolCall")
								container.addChild(new Text(theme.fg("muted", "→ ") + formatToolCall(item.name, item.args, theme.fg.bind(theme)), 0, 0));
						}
						if (finalOutput) {
							container.addChild(new Spacer(1));
							container.addChild(new Markdown(finalOutput.trim(), 0, 0, mdTheme));
						}
					}
					const usageStr = formatUsageStats(r.usage, r.model);
					if (usageStr) {
						container.addChild(new Spacer(1));
						container.addChild(new Text(theme.fg("dim", usageStr), 0, 0));
					}
					return container;
				}

				let text = `${icon} ${theme.fg("toolTitle", theme.bold(r.agent))}${theme.fg("muted", ` (${r.agentSource})`)}`;
				if (isError && r.stopReason) text += ` ${theme.fg("error", `[${r.stopReason}]`)}`;
				if (isError && r.errorMessage) text += `\n${theme.fg("error", `Error: ${r.errorMessage}`)}`;
				else if (displayItems.length === 0) text += `\n${theme.fg("muted", running ? "(running...)" : "(no output)")}`;
				else text += `\n${renderItems(displayItems, COLLAPSED_ITEM_COUNT)}`;
				const usageStr = formatUsageStats(r.usage, r.model);
				if (usageStr) text += `\n${theme.fg("dim", usageStr)}`;
				if (!expanded) text += `\n${expandHint()}`;
				return new Text(text, 0, 0);
			}

			// parallel
			const aggregateUsage = (results: SingleResult[]) => {
				const total = emptyUsage();
				for (const r of results) {
					total.input += r.usage.input;
					total.output += r.usage.output;
					total.cacheRead += r.usage.cacheRead;
					total.cacheWrite += r.usage.cacheWrite;
					total.cost += r.usage.cost;
					total.turns += r.usage.turns;
				}
				return total;
			};

			const running = details.results.filter((r) => isRunning(r)).length;
			const successCount = details.results.filter((r) => !isRunning(r) && !isFailedResult(r)).length;
			const failCount = details.results.filter((r) => !isRunning(r) && isFailedResult(r)).length;
			const icon = running > 0 ? theme.fg("warning", "⏳") : failCount > 0 ? theme.fg("warning", "◐") : theme.fg("success", "✓");
			const status = running > 0
				? `${successCount + failCount}/${details.results.length} done, ${running} running`
				: `${successCount}/${details.results.length} tasks`;

			if (expanded && running === 0) {
				const container = new Container();
				container.addChild(new Text(`${icon} ${theme.fg("toolTitle", theme.bold("parallel "))}${theme.fg("accent", status)}`, 0, 0));
				for (const r of details.results) {
					const rIcon = isFailedResult(r) ? theme.fg("error", "✗") : theme.fg("success", "✓");
					const displayItems = getDisplayItems(r.messages);
					const finalOutput = getFinalOutput(r.messages);
					container.addChild(new Spacer(1));
					container.addChild(new Text(`${theme.fg("muted", "─── ") + theme.fg("accent", r.agent)} ${rIcon}`, 0, 0));
					container.addChild(new Text(theme.fg("muted", "Task: ") + theme.fg("dim", r.task), 0, 0));
					for (const item of displayItems) {
						if (item.type === "toolCall")
							container.addChild(new Text(theme.fg("muted", "→ ") + formatToolCall(item.name, item.args, theme.fg.bind(theme)), 0, 0));
					}
					if (finalOutput) {
						container.addChild(new Spacer(1));
						container.addChild(new Markdown(finalOutput.trim(), 0, 0, mdTheme));
					}
					const taskUsage = formatUsageStats(r.usage, r.model);
					if (taskUsage) container.addChild(new Text(theme.fg("dim", taskUsage), 0, 0));
				}
				const usageStr = formatUsageStats(aggregateUsage(details.results));
				if (usageStr) {
					container.addChild(new Spacer(1));
					container.addChild(new Text(theme.fg("dim", `Total: ${usageStr}`), 0, 0));
				}
				return container;
			}

			let text = `${icon} ${theme.fg("toolTitle", theme.bold("parallel "))}${theme.fg("accent", status)}`;
			for (const r of details.results) {
				const rIcon = isRunning(r) ? theme.fg("warning", "⏳") : isFailedResult(r) ? theme.fg("error", "✗") : theme.fg("success", "✓");
				const displayItems = getDisplayItems(r.messages);
				text += `\n\n${theme.fg("muted", "─── ")}${theme.fg("accent", r.agent)} ${rIcon}`;
				if (displayItems.length === 0) text += `\n${theme.fg("muted", isRunning(r) ? "(running...)" : "(no output)")}`;
				else text += `\n${renderItems(displayItems, 5)}`;
			}
			if (running === 0) {
				const usageStr = formatUsageStats(aggregateUsage(details.results));
				if (usageStr) text += `\n\n${theme.fg("dim", `Total: ${usageStr}`)}`;
			}
			if (!expanded) text += `\n${expandHint()}`;
			return new Text(text, 0, 0);
		},
	});

	// ── subagent_spawn: fire-and-forget background ────────────────────────────

	pi.registerTool({
		name: "subagent_spawn",
		label: "Spawn Background Subagent",
		description:
			"Spawn a background subagent that runs its task to completion without interruption (no timeout, no human-in-the-loop). " +
			"Returns an id immediately. Poll progress/result with subagent_list or subagent_result; a follow-up message is delivered when it finishes. " +
			"The subagent never commits to git — the parent commits its result. Use for long-running independent work.",
		parameters: Type.Object({
			task: Type.String({ description: "Complete task description for the subagent" }),
			agent: Type.Optional(Type.String({ description: "Agent name (from .pi/agents or ~/.pi/agent/agents). Omit for a generic subagent." })),
			model: Type.Optional(Type.String({ description: "Model override (default: AGENTIC_SUBAGENT_MODEL)" })),
			tools: Type.Optional(Type.String({ description: "Comma-separated tool allowlist override" })),
		}),

		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const agent = resolveAgent(params.agent, ctx.cwd);
			if (params.agent && !agent) {
				return {
					content: [{ type: "text", text: `Unknown agent "${params.agent}". Available agents: ${availableAgentNames(ctx.cwd)}` }],
					isError: true,
					details: {},
				};
			}

			const id = nextId("SA");
			const mailboxDir = path.join(ctx.cwd, MAILBOX_ROOT, id);
			const agentName = agent?.name ?? params.agent ?? "generic";
			const toolsOverride = params.tools ? params.tools.split(",").map((s) => s.trim()).filter(Boolean) : undefined;

			const sub: BackgroundSub = { id, agentName, task: params.task, mailboxDir, startedAt: Date.now(), status: "running" };
			background.set(id, sub);
			updateSubagentsWidget();

			runSingleAgent({
				agent,
				agentName,
				task: params.task,
				cwd: ctx.cwd,
				mailboxDir,
				trackProc: (p) => {
					sub.proc = p;
					activeProcs.add(p);
				},
				modelOverride: params.model,
				toolsOverride,
				onUpdate: (r) => {
					const act = formatLastActivity(r);
					if (act) sub.lastActivity = act;
					updateSubagentsWidget();
				},
			})
				.then((result) => {
					sub.status = isFailedResult(result) ? "error" : "done";
					sub.endedAt = result.endedAt;
					sub.exitCode = result.exitCode;
					background.set(id, sub);
					activeProcs.delete(sub.proc!);
					updateSubagentsWidget();
					if (shuttingDown) return;
					const elapsed = Math.round(((result.endedAt ?? Date.now()) - sub.startedAt) / 1000);
					const output = truncateOutput(getResultOutput(result), FOLLOWUP_OUTPUT_CAP);
					const msg =
						`Subagent "${agentName}" (${id}) finished in ${elapsed}s (exit ${result.exitCode}).\n` +
						`Mailbox: ${mailboxDir}\n\nResult:\n${output}`;
					try {
						pi.sendUserMessage(msg, { deliverAs: "followUp" });
					} catch {
						/* extension may be stale after reload; mailbox still has the result */
					}
				})
				.catch((err) => {
					sub.status = "error";
					sub.endedAt = Date.now();
					background.set(id, sub);
					updateSubagentsWidget();
				});

			return {
				content: [
					{
						type: "text",
						text: `Subagent "${agentName}" (${id}) spawned and running in the background.\nMailbox: ${mailboxDir}\nPoll with subagent_list / subagent_result; a follow-up message will arrive on completion.`,
					},
				],
				details: {},
			};
		},
	});

	// ── Management tools ──────────────────────────────────────────────────────

	pi.registerTool({
		name: "subagent_list",
		label: "List Background Subagents",
		description: "List all background subagents with their id, agent, status, and task.",
		parameters: Type.Object({}),
		async execute() {
			if (background.size === 0) return { content: [{ type: "text", text: "No background subagents." }], details: {} };
			const lines = Array.from(background.values()).map((s) => `${s.id} [${s.status.toUpperCase()}] ${s.agentName} — ${s.task}`);
			return { content: [{ type: "text", text: `Background subagents:\n${lines.join("\n")}` }], details: {} };
		},
	});

	pi.registerTool({
		name: "subagent_result",
		label: "Get Subagent Result",
		description: "Read the current status and (if finished) final result of a background subagent by id.",
		parameters: Type.Object({
			id: Type.String({ description: "Subagent id (e.g. SA<timestamp>-<n>)" }),
		}),
		async execute(_toolCallId, params) {
			const sub = background.get(params.id);
			if (!sub) return { content: [{ type: "text", text: `No background subagent with id "${params.id}".` }], isError: true, details: {} };

			let status: "running" | "done" | "error" = sub.status;
			let resultText = "";
			try {
				const statusRaw = fs.readFileSync(path.join(sub.mailboxDir, "status"), "utf-8").trim();
				const first = statusRaw.split(/\s+/)[0];
				if (first === "running" || first === "done" || first === "error") status = first;
				resultText = fs.readFileSync(path.join(sub.mailboxDir, "result.md"), "utf-8");
			} catch {
				/* result not written yet */
			}

			if (status === "running") {
				return { content: [{ type: "text", text: `${sub.id} (${sub.agentName}) is still running.\nMailbox: ${sub.mailboxDir}` }], details: {} };
			}
			return { content: [{ type: "text", text: `${sub.id} (${sub.agentName}) — ${status}\n\n${truncateOutput(resultText || "(no output)", FOLLOWUP_OUTPUT_CAP)}` }], details: {} };
		},
	});

	pi.registerTool({
		name: "subagent_stop",
		label: "Stop Subagent",
		description: "Kill a running background subagent by id (SIGTERM, then SIGKILL).",
		parameters: Type.Object({
			id: Type.String({ description: "Subagent id" }),
		}),
		async execute(_toolCallId, params) {
			const sub = background.get(params.id);
			if (!sub) return { content: [{ type: "text", text: `No background subagent with id "${params.id}".` }], isError: true, details: {} };
			if (sub.proc) {
				killGracefully(sub.proc);
				activeProcs.delete(sub.proc);
			}
			sub.status = "error";
			sub.endedAt = Date.now();
			writeStatus(sub.mailboxDir, "error", "stopped");
			updateSubagentsWidget();
			return { content: [{ type: "text", text: `${sub.id} (${sub.agentName}) stopped.` }], details: {} };
		},
	});

	pi.registerTool({
		name: "subagent_cleanup",
		label: "Clean Up Subagents",
		description: "Remove finished background subagents from the registry and optionally purge old mailbox directories.",
		parameters: Type.Object({
			purge: Type.Optional(Type.Boolean({ description: "Also delete mailbox directories of removed subagents (default: false)" })),
		}),
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			let removed = 0;
			for (const [id, sub] of Array.from(background.entries())) {
				if (sub.status !== "running") {
					background.delete(id);
					if (params.purge) {
						try {
							fs.rmSync(sub.mailboxDir, { recursive: true, force: true });
						} catch {
							/* ignore */
						}
					}
					removed++;
				}
			}
			updateSubagentsWidget();
			return { content: [{ type: "text", text: `Cleaned up ${removed} finished subagent${removed === 1 ? "" : "s"} (${background.size} still running).` }], details: {} };
		},
	});

	// ── /subagents: interactive task view ─────────────────────────────────────

	const subagentDetailLines = (sub: BackgroundSub): string[] => {
		const elapsed = Math.round(((sub.endedAt ?? Date.now()) - sub.startedAt) / 1000);
		const lines: string[] = [
			`Status: ${sub.status}${sub.exitCode !== undefined ? ` (exit ${sub.exitCode})` : ""}`,
			`Elapsed: ${elapsed}s`,
			`Mailbox: ${sub.mailboxDir}`,
			`Task: ${sub.task}`,
		];
		if (sub.lastActivity) lines.push(`Last activity: ${sub.lastActivity}`);
		if (sub.status !== "running") {
			try {
				const resultText = fs.readFileSync(path.join(sub.mailboxDir, "result.md"), "utf-8");
				lines.push("", "Result:", truncateOutput(resultText.trim() || "(no output)", 1500));
			} catch {
				/* result not written yet */
			}
		}
		return lines;
	};

	pi.registerCommand("subagents", {
		description: "Open the interactive background-subagent task view",
		handler: async (_args, ctx) => {
			if (ctx.mode !== "tui") {
				ctx.ui.notify("/subagents requires interactive mode", "error");
				return;
			}
			const subs = Array.from(background.values());
			if (subs.length === 0) {
				ctx.ui.notify("No background subagents.", "info");
				return;
			}

			const items: SelectItem[] = subs.map((s) => {
				const icon = s.status === "running" ? "⏳" : s.status === "done" ? "✓" : "✗";
				const elapsed = Math.round(((s.endedAt ?? Date.now()) - s.startedAt) / 1000);
				return {
					value: s.id,
					label: `${icon} ${s.agentName}`,
					description: `[${s.status}] ${elapsed}s — ${s.task}`,
				};
			});

			const selected = await ctx.ui.custom<string | null>((tui, theme, _kb, done) => {
				const container = new Container();
				container.addChild(new DynamicBorder((s: string) => theme.fg("accent", s)));
				container.addChild(new Text(theme.fg("accent", theme.bold("Subagents")), 1, 0));
				const selectList = new SelectList(items, Math.min(items.length, 10), {
					selectedPrefix: (t) => theme.fg("accent", t),
					selectedText: (t) => theme.fg("accent", t),
					description: (t) => theme.fg("muted", t),
					scrollInfo: (t) => theme.fg("dim", t),
					noMatch: (t) => theme.fg("warning", t),
				});
				selectList.onSelect = (item) => done(item.value);
				selectList.onCancel = () => done(null);
				container.addChild(selectList);
				container.addChild(new Text(theme.fg("dim", "↑↓ navigate • enter details • esc close"), 1, 0));
				container.addChild(new DynamicBorder((s: string) => theme.fg("accent", s)));
				return {
					render: (w) => container.render(w),
					invalidate: () => container.invalidate(),
					handleInput: (data) => {
						selectList.handleInput(data);
						tui.requestRender();
					},
				};
			}, { overlay: true });

			if (!selected) return;
			const sub = background.get(selected);
			if (!sub) return;

			await ctx.ui.custom<void>((_tui, theme, _kb, done) => {
				const container = new Container();
				container.addChild(new DynamicBorder((s: string) => theme.fg("accent", s)));
				container.addChild(new Text(theme.fg("accent", theme.bold(`${sub.agentName} (${sub.id})`)), 1, 0));
				for (const line of subagentDetailLines(sub)) {
					container.addChild(new Text(theme.fg("muted", line), 1, 0));
				}
				container.addChild(new Text(theme.fg("dim", "esc / enter close"), 1, 0));
				container.addChild(new DynamicBorder((s: string) => theme.fg("accent", s)));
				return {
					render: (w) => container.render(w),
					invalidate: () => container.invalidate(),
					handleInput: (data) => {
						if (matchesKey(data, Key.escape) || matchesKey(data, Key.enter)) done();
					},
				};
			}, { overlay: true });
		},
	});
}

async function mapWithConcurrencyLimit<TIn, TOut>(
	items: TIn[],
	concurrency: number,
	fn: (item: TIn, index: number) => Promise<TOut>,
): Promise<TOut[]> {
	if (items.length === 0) return [];
	const limit = Math.max(1, Math.min(concurrency, items.length));
	const results: TOut[] = new Array(items.length);
	let nextIndex = 0;
	const workers = new Array(limit).fill(null).map(async () => {
		while (true) {
			const current = nextIndex++;
			if (current >= items.length) return;
			results[current] = await fn(items[current], current);
		}
	});
	await Promise.all(workers);
	return results;
}

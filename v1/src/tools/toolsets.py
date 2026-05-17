from tools.toolset import Toolset
from routing.toolset_patterns import (
    ANALYSIS_PATTERN,
    SEARCH_PATTERN,
    GIT_PATTERN,
    BRIEFBOT_PATTERN,
    REVERSING_PATTERN,
    SYMBOLIC_PATTERN,
    CONTAINER_PATTERN,
)
from tools.implementations.file_io.read_file import ReadFileTool
from tools.implementations.file_io.write_file import WriteFileTool
from tools.implementations.file_io.list_files import ListFilesTool
from tools.implementations.file_io.walk_directory import WalkDirectoryTool
from tools.implementations.file_io.copy_file import CopyFileTool
from tools.implementations.file_io.move_file import MoveFileTool
from tools.implementations.file_io.delete_file import DeleteFileTool
from tools.implementations.file_io.delete_directory import DeleteDirectoryTool
from tools.implementations.file_io.make_directory import MakeDirectoryTool
from tools.implementations.file_io.read_file_lines import ReadFileLinesTool
from tools.implementations.file_io.get_working_directory import GetWorkingDirectoryTool
from tools.implementations.file_io.environment_info import EnvironmentInfoTool
from tools.implementations.file_io.download_file import DownloadFileTool
from tools.implementations.shell.bash_exec import BashExecTool
from tools.implementations.shell.search_files import SearchFilesTool
from tools.implementations.analysis.strings_tool import StringsTool
from tools.implementations.analysis.objdump_tool import ObjdumpTool
from tools.implementations.analysis.file_info import FileInfoTool
from tools.implementations.analysis.hexdump_tool import HexdumpTool
from tools.implementations.analysis.nm_tool import NmTool
from tools.implementations.analysis.ltrace_tool import LtraceTool
from tools.implementations.analysis.strace_tool import StraceTool
from tools.implementations.analysis.readelf_tool import ReadElfTool
from tools.implementations.analysis.checksec_tool import ChecksecTool
from tools.implementations.analysis.grep_binary import GrepBinaryTool
from tools.implementations.crypto.hash_file import HashFileTool
from tools.implementations.crypto.base64_tool import Base64EncodeTool, Base64DecodeTool
from tools.implementations.crypto.xor_decode import XorDecodeTool
from tools.implementations.web.http_request import HttpRequestTool
from tools.implementations.web.read_url import ReadUrlTool
from tools.implementations.web.extract_html import ExtractHtmlTool
from tools.implementations.data.dataframe_load import DataframeLoadTool
from tools.implementations.data.dataframe_query import DataframeQueryTool
from tools.implementations.data.json_query import JsonQueryTool
from tools.implementations.data.regex_match import RegexMatchTool
from tools.implementations.data.diff_files import DiffFilesTool
from tools.implementations.data.template_render import TemplateRenderTool
from tools.implementations.artifacts.list_artifacts import ListArtifactsTool
from tools.implementations.artifacts.get_artifact import GetArtifactTool
from tools.implementations.artifacts.store_artifact import StoreArtifactTool
from tools.implementations.artifacts.expel_artifact import ExpelArtifactTool
from tools.implementations.artifacts.artifact_info import ArtifactInfoTool
from tools.implementations.artifacts.recall_sessions import RecallSessionsTool
from tools.implementations.search.web_search import WebSearchTool
from tools.implementations.search.news_search import NewsSearchTool
from tools.implementations.search.image_search import ImageSearchTool
from tools.implementations.git.git_status import GitStatusTool
from tools.implementations.git.git_log import GitLogTool
from tools.implementations.git.git_diff import GitDiffTool
from tools.implementations.git.git_show import GitShowTool
from tools.implementations.git.git_blame import GitBlameTool
from tools.implementations.git.git_branch import GitBranchTool
from tools.implementations.git.git_stash import GitStashTool
from tools.implementations.document.read_pdf import ReadPdfTool
from tools.implementations.document.read_docx import ReadDocxTool
from tools.implementations.document.document_info import DocumentInfoTool
from tools.implementations.document.read_epub import ReadEpubTool
from tools.implementations.briefbot.briefbot_search import BriefbotSearchTool
from tools.implementations.briefbot.briefbot_trending import BriefbotTrendingTool
from tools.implementations.briefbot.briefbot_item import BriefbotItemTool
from tools.implementations.reversing.r2_functions import R2FunctionsTool
from tools.implementations.reversing.r2_disassemble import R2DisassembleTool
from tools.implementations.reversing.r2_decompile import R2DecompileTool
from tools.implementations.reversing.r2_callgraph import R2CallgraphTool
from tools.implementations.reversing.r2_xrefs import R2XrefsTool
from tools.implementations.reversing.r2_imports import R2ImportsTool
from tools.implementations.reversing.r2_constants import R2ConstantsTool
from tools.implementations.reversing.ghidra_analyze import GhidraAnalyzeTool
from tools.implementations.reversing.ghidra_functions import GhidraFunctionsTool
from tools.implementations.reversing.ghidra_decompile import GhidraDecompileTool
from tools.implementations.reversing.ghidra_callgraph import GhidraCallgraphTool
from tools.implementations.reversing.ghidra_find_constants import GhidraFindConstantsTool
from tools.implementations.reversing.lldb_trace import LLDBTraceTool
from tools.implementations.reversing.lldb_step import LLDBStepTool
from tools.implementations.symbolic.angr_reachable import AngrReachableTool
from tools.implementations.symbolic.angr_solve import AngrSolveTool
from tools.implementations.symbolic.angr_constraints import AngrConstraintsTool
from tools.implementations.symbolic.angr_explore import AngrExploreTool
from tools.implementations.container.tools import RunTargetTool, DiffBehaviorTool, FuzzTargetTool
from tools.implementations.container.runtime import ContainerSession
from shared_types import RoutingRule
from routing.conditions import has_file_path, has_extension, any_keyword, last_tools_were, all_of


FILE_IO = Toolset(
    name="file_io",
    description="File system read/write/navigation tools",
    planning_note="Use download_file only for saving binary files to disk. To fetch and read web pages, use read_url from the web toolset instead.",
    tools=[
        ReadFileTool(),
        WriteFileTool(),
        ListFilesTool(),
        WalkDirectoryTool(),
        CopyFileTool(),
        MoveFileTool(),
        DeleteFileTool(),
        DeleteDirectoryTool(),
        MakeDirectoryTool(),
        ReadFileLinesTool(),
        GetWorkingDirectoryTool(),
        EnvironmentInfoTool(),
        DownloadFileTool(),
    ],
    rules=[
        RoutingRule(toolset="file_io", condition=has_file_path()),
        RoutingRule(toolset="file_io", condition=has_extension(
            ".py", ".txt", ".md", ".json", ".yaml", ".yml",
            ".csv", ".log", ".sh", ".c", ".cpp", ".h", ".rs", ".go", ".js", ".ts",
        )),
        RoutingRule(toolset="file_io", condition=any_keyword(
            "read", "write", "file", "directory", "folder", "list", "ls",
            "copy", "move", "delete", "rename", "mkdir", "download", "save",
            "open", "create", "path", "cwd", "pwd",
        )),
    ],
)

SHELL = Toolset(
    name="shell",
    description="Shell command execution and file search",
    tools=[
        BashExecTool(),
        SearchFilesTool(),
    ],
    rules=[
        RoutingRule(toolset="shell", condition=any_keyword(
            "run", "execute", "bash", "shell", "command", "script",
            "terminal", "process", "grep", "find", "search", "pipe",
            "install", "build", "compile", "make",
        )),
    ],
)

ANALYSIS = Toolset(
    name="analysis",
    description="Binary analysis and reverse engineering tools",
    tools=[
        StringsTool(),
        ObjdumpTool(),
        FileInfoTool(),
        HexdumpTool(),
        NmTool(),
        LtraceTool(),
        StraceTool(),
        ReadElfTool(),
        ChecksecTool(),
        GrepBinaryTool(),
    ],
    rules=[
        RoutingRule(toolset="analysis", condition=has_extension(
            ".o", ".so", ".a", ".dylib", ".elf", ".bin", ".exe", ".out",
        )),
        RoutingRule(toolset="analysis", condition=any_keyword(
            "binary", "disassemble", "disassembly", "objdump", "strings",
            "hexdump", "hex", "symbols", "nm", "readelf", "elf",
            "ltrace", "strace", "checksec", "reverse", "malware",
            "executable", "segment", "section", "header",
            "identify", "filetype", "architecture", "arch",
        )),
        RoutingRule(
            toolset="analysis",
            condition=lambda msg, _: bool(ANALYSIS_PATTERN.search(msg)),
        ),
        RoutingRule(toolset="analysis", condition=last_tools_were(
            "strings", "objdump", "hexdump", "nm", "readelf",
            "ltrace", "strace", "checksec", "grep_binary", "file_info",
        )),
    ],
)

CRYPTO = Toolset(
    name="crypto",
    description="Hashing, encoding, and basic cryptanalysis tools",
    tools=[
        HashFileTool(),
        Base64EncodeTool(),
        Base64DecodeTool(),
        XorDecodeTool(),
    ],
    rules=[
        RoutingRule(toolset="crypto", condition=any_keyword(
            "hash", "md5", "sha", "sha256", "checksum",
            "base64", "encode", "decode", "xor", "encrypt", "decrypt",
            "hex", "bytes", "cipher", "crypto", "cryptography",
        )),
        RoutingRule(toolset="crypto", condition=last_tools_were(
            "hash_file", "base64_encode", "base64_decode", "xor_decode",
        )),
    ],
)


WEB = Toolset(
    name="web",
    description="HTTP requests, web page fetching, and HTML extraction",
    planning_note="Use read_url to fetch any URL (webpage, article, paper) — it extracts clean text and stores it as an artifact. Use http_request for structured API calls. Use extract_html for CSS selector scraping. Never use download_file, curl, or wget to fetch web pages.",
    tools=[
        HttpRequestTool(),
        ReadUrlTool(),
        ExtractHtmlTool(),
    ],
    rules=[
        RoutingRule(toolset="web", condition=any_keyword(
            "http", "https", "url", "fetch", "request", "api", "endpoint",
            "website", "webpage", "web page", "scrape", "crawl", "browse",
            "post", "get request", "rest", "curl", "html", "download page",
            "read page", "read url", "read article", "read blog",
        )),
    ],
)

DATA = Toolset(
    name="data",
    description="Data processing tools: dataframes, JSONPath, regex, diff, and templates",
    planning_note=(
        "Use dataframe_load to load CSV/TSV/JSON/Parquet data into a dataframe artifact. "
        "Use dataframe_query for pandas transformations over existing dataframe artifacts. "
        "Use json_query for JSONPath extraction, regex_match for pattern extraction or replacement, "
        "diff_files for text diffs, and template_render for Jinja2 rendering."
    ),
    tools=[
        DataframeLoadTool(),
        DataframeQueryTool(),
        JsonQueryTool(),
        RegexMatchTool(),
        DiffFilesTool(),
        TemplateRenderTool(),
    ],
    rules=[
        RoutingRule(toolset="data", condition=has_extension(".csv", ".tsv", ".parquet", ".json", ".jsonl")),
        RoutingRule(toolset="data", condition=any_keyword(
            "dataframe", "pandas", "csv", "tsv", "parquet", "jsonpath", "json",
            "filter", "aggregate", "groupby", "join", "merge", "pivot",
            "regex", "pattern", "extract", "replace", "diff", "compare",
            "template", "render", "jinja",
        )),
    ],
)

ARTIFACTS = Toolset(
    name="artifacts",
    description="Manage named artifacts produced during the current session",
    planning_note=(
        "Use list_artifacts to discover available artifacts. "
        "Use get_artifact to read a stored value by key. "
        "Use store_artifact to save an intermediate value for later steps. "
        "Use artifact_info for metadata-only inspection, expel_artifact to delete, "
        "and recall_sessions to find relevant prior sessions and artifacts."
    ),
    tools=[
        ListArtifactsTool(),
        GetArtifactTool(),
        StoreArtifactTool(),
        ExpelArtifactTool(),
        ArtifactInfoTool(),
        RecallSessionsTool(),
    ],
    rules=[
        RoutingRule(toolset="artifacts", condition=any_keyword(
            "artifact", "artifacts", "stored", "recall", "expel",
            "artifact_info", "get_artifact", "store_artifact", "list_artifacts",
            "recall_sessions", "previous sessions", "have we done this before",
            "prior session", "past session", "earlier session",
        )),
    ],
)

SEARCH = Toolset(
    name="search",
    description="Web, news, and image search via the Brave Search API",
    planning_note=(
        "Use web_search to find information on any topic without a specific URL. "
        "Use news_search for current events, recent developments, or dated articles. "
        "Use image_search when looking for image assets or confirming visual content. "
        "After web_search, use read_url to fetch and read the full content of a specific result."
    ),
    tools=[
        WebSearchTool(),
        NewsSearchTool(),
        ImageSearchTool(),
    ],
    rules=[
        RoutingRule(toolset="search", condition=any_keyword(
            "search", "look up", "look for", "find information", "google",
            "search the web", "web search", "find articles", "find pages",
            "current events", "latest news", "recent", "news about",
            "image search", "find images", "find pictures",
        )),
        RoutingRule(
            toolset="search",
            condition=lambda msg, _: bool(SEARCH_PATTERN.search(msg)),
        ),
    ],
)

GIT = Toolset(
    name="git",
    description="Read-only git source control introspection tools",
    planning_note=(
        "Use git_status to check the working tree state. "
        "Use git_log to browse commit history. "
        "Use git_diff to see changes — set staged=true for the index. "
        "Use git_show for a specific commit (default HEAD). "
        "Use git_blame to see who changed which lines. "
        "Use git_branch to list branches. "
        "For write operations (commit, push, checkout, reset), use bash_exec."
    ),
    tools=[
        GitStatusTool(),
        GitLogTool(),
        GitDiffTool(),
        GitShowTool(),
        GitBlameTool(),
        GitBranchTool(),
        GitStashTool(),
    ],
    rules=[
        RoutingRule(toolset="git", condition=any_keyword(
            "git", "commit", "branch", "diff", "blame", "stash",
            "git log", "git status", "git diff", "git show",
            "commit history", "who changed", "what changed",
            "working tree", "staged", "unstaged", "repository",
        )),
        RoutingRule(
            toolset="git",
            condition=lambda msg, _: bool(GIT_PATTERN.search(msg)),
        ),
    ],
)

DOCUMENT = Toolset(
    name="document",
    description="Text extraction from PDF, DOCX, and EPUB documents",
    planning_note=(
        "Use read_pdf to extract text from PDF files (supports page ranges). "
        "Use read_docx for Word documents (.docx). "
        "Use read_epub for e-books (.epub). "
        "Use document_info for quick metadata without extracting full text. "
        "Always set artifact_key when extracting long documents — "
        "use get_artifact or read_file_lines to read the content in chunks afterward."
    ),
    tools=[
        ReadPdfTool(),
        ReadDocxTool(),
        DocumentInfoTool(),
        ReadEpubTool(),
    ],
    rules=[
        RoutingRule(toolset="document", condition=has_extension(
            ".pdf", ".docx", ".doc", ".epub",
        )),
        RoutingRule(toolset="document", condition=any_keyword(
            "pdf", "docx", "word document", "word doc", "epub", "ebook",
            "read pdf", "extract pdf", "open pdf", "read document",
            "document text", "pdf text", "pages", "read ebook",
        )),
    ],
)

BRIEFBOT = Toolset(
    name="briefbot",
    description="Search and browse the local Briefbot research corpus (nightly-indexed papers, blogs, and tech news)",
    planning_note=(
        "RESEARCH RADAR PATTERN — use this for 'what's new/hot/trending' questions: "
        "(1) Call briefbot_trending first to get trending clusters (velocity_3d, trend_score) and hot topics (momentum) — "
        "this surfaces what is RISING RIGHT NOW, not just what exists. "
        "(2) For each interesting cluster, call briefbot_search with category='papers' or category='ai_research', "
        "order_by='date', days=14 to find the specific papers/posts driving it. "
        "(3) For the top 2-3 results, call briefbot_item to get the full record including opportunity analysis. "
        "(4) A synthesis step (action_type=conversation, tool=null) must explain WHY each finding is notable, "
        "what problem it solves, and what the signal strength is (velocity/trend_score). "
        "DO NOT stop after a single briefbot_search for research/trend queries — that is too shallow. "
        "Use briefbot_trending as the entry point for any 'what's new in X', 'what's hot', "
        "'what should I know about', or 'what's gaining traction' question. "
        "Prefer briefbot over web_search for research queries — corpus is scored and deduped. "
        "Fall back to web_search only if briefbot returns no results."
    ),
    tools=[
        BriefbotSearchTool(),
        BriefbotTrendingTool(),
        BriefbotItemTool(),
    ],
    rules=[
        RoutingRule(toolset="briefbot", condition=any_keyword(
            "briefbot", "research corpus", "research papers", "arxiv", "papers",
            "what's trending", "what is trending", "what's hot", "trending in ai",
            "trending in ml", "what's new in ai", "this week in ai",
            "find papers", "find articles", "find research",
            "local corpus", "indexed content",
        )),
        RoutingRule(
            toolset="briefbot",
            condition=lambda msg, _: bool(BRIEFBOT_PATTERN.search(msg)),
        ),
    ],
)

REVERSING = Toolset(
    name="reversing",
    description="Deep binary structural and dynamic analysis using radare2, Ghidra, and LLDB",
    planning_note=(
        "Use r2_functions for full function inventory, r2_disassemble for one function's assembly, "
        "r2_decompile for pseudocode (degrades to asm if r2ghidra not installed), "
        "r2_callgraph for call relationships, r2_xrefs for what calls a given address, "
        "r2_imports for library dependencies, r2_constants for strings-with-addresses. "
        "Prefer r2_disassemble/r2_callgraph over objdump/nm for structural questions. "
        "Ghidra tools (ghidra_decompile, ghidra_functions, ghidra_callgraph, ghidra_find_constants) "
        "produce higher-quality output but require the first-run analysis (30-60s). "
        "When decompiling, use ghidra_decompile with the 'function' argument to decompile ONE "
        "specific function rather than all — much smaller output, less token cost. "
        "LLDB tools (lldb_trace, lldb_step) observe runtime behavior with concrete register values. "
        "Use lldb_trace to capture register state at key breakpoints (function entry/exit, loop start). "
        "Use lldb_step to walk through an inner loop instruction by instruction. "
        "LLDB output is small (~200 chars per hit) — always prefer dynamic observation over "
        "static decompile when a reconstruction goal exists and oracle inputs are known."
    ),
    tools=[
        R2FunctionsTool(),
        R2DisassembleTool(),
        R2DecompileTool(),
        R2CallgraphTool(),
        R2XrefsTool(),
        R2ImportsTool(),
        R2ConstantsTool(),
        GhidraAnalyzeTool(),
        GhidraFunctionsTool(),
        GhidraDecompileTool(),
        GhidraCallgraphTool(),
        GhidraFindConstantsTool(),
        LLDBTraceTool(),
        LLDBStepTool(),
    ],
    rules=[
        RoutingRule(toolset="reversing", condition=any_keyword(
            "decompile", "decompiled", "pseudocode", "call graph", "callgraph",
            "what functions", "list functions", "function list", "function names",
            "what calls", "xref", "cross-reference", "cross reference",
            "radare2", "r2", "ghidra", "r2_functions", "r2_disassemble",
            "structural analysis", "program structure", "who calls",
            "lldb", "gdb", "debugger", "breakpoint", "register",
            "trace execution", "step through", "runtime", "dynamic analysis",
            "watch execution", "observe", "lldb_trace", "lldb_step",
        )),
        RoutingRule(
            toolset="reversing",
            condition=lambda msg, _: bool(REVERSING_PATTERN.search(msg)),
        ),
    ],
)

SYMBOLIC = Toolset(
    name="symbolic",
    description="Symbolic execution and constraint solving using angr",
    planning_note=(
        "Use angr_reachable to check if a function/address is reachable from entry. "
        "Use angr_solve to find the input (stdin or argv) that reaches a success state and avoids failure states. "
        "Use angr_constraints to see what conditions must hold to reach a target. "
        "Use angr_explore for open-ended questions not covered by the above templates. "
        "Always run analysis/reversing recon first to get addresses — angr needs concrete addresses. "
        "Binary complexity scales the timeout automatically. "
        "Requires: pip install angr"
    ),
    tools=[
        AngrReachableTool(),
        AngrSolveTool(),
        AngrConstraintsTool(),
        AngrExploreTool(),
    ],
    rules=[
        RoutingRule(toolset="symbolic", condition=any_keyword(
            "angr", "symbolic", "symbolic execution", "reachable", "reach",
            "solve", "find input", "find password", "find key", "crack",
            "what input", "what conditions", "path condition", "constraint",
            "prove", "buffer overflow", "vulnerable sink", "trigger",
            "generate test case", "test case generation", "crackme",
        )),
        RoutingRule(
            toolset="symbolic",
            condition=lambda msg, _: bool(SYMBOLIC_PATTERN.search(msg)),
        ),
    ],
)

CONTAINER = Toolset(
    name="container",
    description="Containerized dynamic analysis and differential behavioral testing",
    planning_note=(
        "Use diff_behavior to compare an oracle binary against a reconstructed candidate "
        "(source or binary) across multiple test cases. The oracle runs on host; the candidate "
        "is compiled and run inside Docker/Podman for isolation. "
        "Read the DiffReport: all_match=true means verified; for failures, read mismatch_summary "
        "per case to identify the bug (padding length, CBC chaining, key derivation), fix the "
        "source file, then call diff_behavior again. Repeat until all_match=true. "
        "Use run_target to explore how a single binary or source behaves with various inputs. "
        "Use fuzz_target to auto-generate boundary/random test cases and diff in one call. "
        "Requires Docker or Podman. If unavailable, these tools return an error."
    ),
    tools=[
        RunTargetTool(),
        DiffBehaviorTool(),
        FuzzTargetTool(),
    ],
    rules=[
        RoutingRule(toolset="container", condition=any_keyword(
            "diff_behavior", "run_target", "fuzz_target",
            "differential", "behavioral testing", "compare behavior",
            "verify reconstruction", "iterate on the code", "test the clone",
            "round-trip test", "does it match", "oracle", "candidate",
        )),
        RoutingRule(
            toolset="container",
            condition=lambda msg, _: bool(CONTAINER_PATTERN.search(msg)),
        ),
    ],
)

# 0090d — Sub-agent toolset. Wraps each registered SubAgentSpec as a
# SubAgentTool so the planner can include `subagent_<name>` steps in plans.
# Built by ``_build_subagent_toolset`` at module import time so the
# child-registry filter has stable identity to filter against.
def _build_subagent_toolset() -> Toolset:
    # Trigger registration of all built-in sub-agent specs.
    from tools.implementations.subagents import ghidra_analyst as _ga  # noqa: F401
    from runtime.subagents.registry import all_specs
    from tools.implementations.subagents.tool import SubAgentTool
    tools = [SubAgentTool(spec) for spec in all_specs()]
    return Toolset(
        name="subagent",
        description=(
            "Sub-agent dispatch tools. Each tool spawns a scoped child agent "
            "with its own toolset, context window, and (optionally) provider. "
            "Use these to delegate context-heavy work like binary analysis or "
            "code generation without polluting the main agent's context."
        ),
        tools=tools,
        rules=[],
        planning_note=(
            "Prefer ``subagent_*`` tools for context-heavy specialist work "
            "(e.g., ``subagent_ghidra_analyst`` for reverse engineering) "
            "instead of running the underlying tools directly. The sub-agent "
            "returns a concise structured summary that fits in your context "
            "easily."
        ),
    )


SUBAGENT = _build_subagent_toolset()

ALL_TOOLSETS = [FILE_IO, SHELL, ANALYSIS, CRYPTO, WEB, DATA, ARTIFACTS, SEARCH, GIT, DOCUMENT, BRIEFBOT, REVERSING, SYMBOLIC, CONTAINER, SUBAGENT]

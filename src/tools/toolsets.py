import re
from tools.toolset import Toolset
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
            condition=lambda msg, _: bool(re.search(
                r"\bwhat\s+(?:kind|type|sort)\s+of\s+(?:file|binary|program)\b"
                r"|\bwhat\s+is\s+this\s+(?:file|binary|program)\b"
                r"|\bfile\s+type\b"
                r"|\bwhat(?:'s|'s| is)\s+(?:the\s+)?(?:file\s+)?(?:type|format|architecture)\b",
                msg, re.IGNORECASE,
            )),
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
            condition=lambda msg, _: bool(re.search(
                r"\bsearch\s+(?:the\s+)?(?:web|internet|online)\b"
                r"|\bfind\s+(?:me\s+)?(?:information|articles|images|news)\b"
                r"|\bwhat(?:'s|'s| is)\s+(?:the\s+)?(?:latest|current)\b"
                r"|\blook\s+(?:it\s+)?up\b",
                msg, re.IGNORECASE,
            )),
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
            condition=lambda msg, _: bool(re.search(
                r"\bgit\s+\w+\b|\bcommit\s+history\b|\bworking\s+tree\b"
                r"|\bwho\s+(?:wrote|added|changed|modified)\b",
                msg, re.IGNORECASE,
            )),
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

ALL_TOOLSETS = [FILE_IO, SHELL, ANALYSIS, CRYPTO, WEB, DATA, ARTIFACTS, SEARCH, GIT, DOCUMENT]

"""Narrowly scoped, budget-enforced tools. Graph nodes call these in trusted
code; the same objects can be bound for an LLM planning loop, but production
graphs use explicit routing - never an unconstrained tool loop.

The LLM has NO tool for: schedules, recipients, credentials, schemas, mail.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .sandbox import SandboxExecutor, SandboxUnavailable


@dataclass
class Budget:
    max_search_calls: int = 24
    max_fetched_pages: int = 40
    max_tool_iterations: int = 12
    searches: int = 0
    fetches: int = 0
    iterations: int = 0
    exceeded: list[str] = field(default_factory=list)

    def check(self, kind: str) -> bool:
        used, cap = {
            "search": (self.searches, self.max_search_calls),
            "fetch": (self.fetches, self.max_fetched_pages),
            "iteration": (self.iterations, self.max_tool_iterations),
        }[kind]
        if used >= cap:
            self.exceeded.append(kind)
            return False
        return True


class SearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    max_results: int = Field(default=8, ge=1, le=20)
    recency_days: int | None = Field(default=None, ge=1, le=180)
    include_domains: list[str] = Field(default_factory=list, max_length=30)


class FetchIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class LookupIn(BaseModel):
    history_days: int = Field(default=60, ge=1, le=365)


class AnalysisIn(BaseModel):
    code: str = Field(min_length=1, max_length=20000)
    inputs: dict[str, str] = Field(default_factory=dict)


class AgentTools:
    """Holds adapters + budget; every method validates and bounds its output."""

    def __init__(self, *, cfg, db, search, fetcher, sandbox: SandboxExecutor):
        self.cfg = cfg
        self.db = db
        self.search = search
        self.fetcher = fetcher
        self.sandbox = sandbox
        self.budget = Budget(cfg.budgets.max_search_calls,
                             cfg.budgets.max_fetched_pages,
                             cfg.budgets.max_tool_iterations)

    # -- public search ------------------------------------------------------- #
    def public_search(self, args: SearchIn) -> list[dict]:
        if not self.budget.check("search"):
            return [{"error": "search budget exhausted"}]
        self.budget.searches += 1
        kwargs = {"max_results": args.max_results, "recency_days": args.recency_days}
        if args.include_domains:
            kwargs["include_domains"] = args.include_domains
        results = self.search.search(args.query, **kwargs)
        return [r.model_dump(mode="json") for r in results]

    # -- controlled fetch ------------------------------------------------------ #
    def fetch_source(self, args: FetchIn) -> dict:
        if not self.budget.check("fetch"):
            return {"status": "skipped", "error": "fetch budget exhausted"}
        self.budget.fetches += 1
        doc = self.fetcher.fetch(args.url)
        out = doc.model_dump(mode="json")
        out["text"] = out["text"][:20000]  # bounded tool output
        from .quality import prepare_source
        return prepare_source(out)

    # -- prior developments ----------------------------------------------------- #
    def lookup_prior_developments(self, args: LookupIn) -> list[dict]:
        rows = self.db.prior_index(args.history_days)
        return [{"title": r["title"], "cluster_id": r["cluster_id"],
                 "published_at": r["published_at"]} for r in rows[:100]]

    # -- sandboxed python --------------------------------------------------------- #
    def run_python_analysis(self, args: AnalysisIn) -> dict:
        """Arbitrary analysis code runs ONLY inside the isolation boundary."""
        if not self.cfg.sandbox.enabled:
            return {"ok": False, "error": "sandbox disabled; generated code not executed"}
        try:
            result = self.sandbox.run(code=args.code, inputs=args.inputs)
        except SandboxUnavailable as exc:
            return {"ok": False, "error": f"sandbox unavailable; execution refused: {exc}"}
        return {"ok": result.ok, "stdout": result.stdout, "stderr": result.stderr,
                "artifacts": [a.__dict__ for a in result.artifacts],
                "error": result.error, "timed_out": result.timed_out}

    # -- LangChain tool bindings --------------------------------------------------- #
    def as_langchain_tools(self):
        from langchain_core.tools import tool

        @tool(args_schema=SearchIn)
        def public_search(query: str, max_results: int = 8, recency_days: int | None = None,
                          include_domains: list[str] | None = None):
            """Search the public internet via the configured provider (budgeted)."""
            return self.public_search(SearchIn(query=query, max_results=max_results,
                                               recency_days=recency_days, include_domains=include_domains or []))

        @tool(args_schema=FetchIn)
        def fetch_source(url: str):
            """Fetch and extract one public URL with SSRF protections (budgeted)."""
            return self.fetch_source(FetchIn(url=url))

        @tool(args_schema=LookupIn)
        def lookup_prior_developments(history_days: int = 60):
            """Look up already-known developments to avoid duplicates."""
            return self.lookup_prior_developments(LookupIn(history_days=history_days))

        @tool(args_schema=AnalysisIn)
        def run_python_analysis(code: str, inputs: dict[str, str] = {}):
            """Run Python analysis INSIDE the no-network Docker sandbox."""
            return self.run_python_analysis(AnalysisIn(code=code, inputs=inputs))

        return [public_search, fetch_source, lookup_prior_developments, run_python_analysis]

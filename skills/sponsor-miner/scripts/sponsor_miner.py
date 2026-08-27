#!/usr/bin/env python3
"""Mine sponsor leads for a target GitHub repo.

Pipeline:
  1. Build a seed list of peer repos whose audience matches the target
     (explicit seeds + GitHub topic search sorted by stars).
  2. Extract sponsors from each seed repo via four sources:
       - README sponsor/backer sections (markdown links)
       - sponsor SVG images (sponsorkit-style, links embedded in the SVG)
       - GitHub Sponsors GraphQL API (public sponsors of the repo owner and
         any accounts listed in FUNDING.yml or linked via github.com/sponsors)
       - Open Collective API (organization backers, with donation totals)
  3. Aggregate sponsorship edges by company and rank by cross-repo
     frequency, verified-payment evidence, entity type, and amounts.

Requires: python3 >= 3.9 and an authenticated GitHub CLI (`gh auth token`).
The Open Collective API needs no key.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

GITHUB_API = "https://api.github.com"
OPEN_COLLECTIVE_API = "https://api.opencollective.com/graphql/v2"
USER_AGENT = "sponsor-miner-skill/0.2.0"

FALLBACK_TOPICS = ["ai-agents", "llm", "developer-tools", "cli"]

SEARCH_SLEEP = 2.1  # repo search allows 30 requests/min
REST_SLEEP = 0.25
GRAPHQL_SLEEP = 0.5
OC_SLEEP = 1.0

MAX_SPONSOR_PAGES = 3  # 100 sponsors per page
MAX_OC_PAGES = 3
MAX_SVGS_PER_REPO = 3
MAX_GITHUB_LOGINS_PER_REPO = 50
MAX_EDGES_PER_REPO = 400
PROFILE_RESOLVE_CAP = 120
GRAPHQL_OWNER_CAP = 80
OC_MIN_TOTAL = 50.0
SVG_MAX_BYTES = 5_000_000

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SPONSOR_HEADING_RE = re.compile(r"\bsponsor|\bbacker|\bsupporter|\bpartner|\bfunding|thanks to", re.IGNORECASE)
SPONSOR_LINE_RE = re.compile(
    r"sponsored by|thanks to our sponsors?|our (?:\w+ )?sponsors|special thanks to|brought to you by|supported by",
    re.IGNORECASE,
)
HTML_SPONSOR_HEADING_RE = re.compile(r"<h[1-6][^>]*>[^<]*(?:sponsor|backer)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")
MD_LINK_RE = re.compile(r"\[([^\]\[]+)\]\(\s*<?(https?://[^)\s>]+)>?\s*\)")
SVG_HREF_RE = re.compile(r"(?:xlink:)?href=\"(https?://[^\"]+)\"")
GITHUB_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?")
OC_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")

RESERVED_GITHUB_PATHS = {
    "about", "apps", "collections", "contact", "events", "explore", "features",
    "join", "login", "marketplace", "notifications", "orgs", "pricing",
    "readme", "security", "settings", "site", "sponsors", "topics", "trending",
}
RESERVED_OC_PATHS = {"become-a-sponsor", "how-it-works", "pricing", "redeem", "search", "signin"}

# Domains that are never a sponsor company: badges, asset hosts, donation
# channels (mined separately, not lead identities), social/media links,
# package registries.
IGNORED_DOMAINS = {
    "img.shields.io", "shields.io", "badge.fury.io", "badgen.net", "nodei.co",
    "avatars.githubusercontent.com", "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com", "raw.githubusercontent.com",
    "camo.githubusercontent.com", "cdn.jsdelivr.net", "unpkg.com",
    "imgur.com", "i.imgur.com",
    "patreon.com", "ko-fi.com", "buymeacoffee.com", "liberapay.com",
    "paypal.com", "paypal.me", "paypalobjects.com", "donorbox.org",
    "issuehunt.io", "tidelift.com", "thanks.dev", "polar.sh", "boosty.to",
    "afdian.com", "afdian.net",
    "twitter.com", "x.com", "discord.gg", "discord.com", "youtube.com",
    "youtu.be", "medium.com", "dev.to", "reddit.com", "t.me", "linkedin.com",
    "facebook.com", "instagram.com", "mastodon.social", "bsky.app",
    "npmjs.com", "pypi.org", "crates.io", "docs.rs", "gitter.im",
    "stackoverflow.com", "github.blog",
}

SPONSORS_QUERY = """
query($login: String!, $after: String) {
  repositoryOwner(login: $login) {
    ... on User {
      sponsorshipsAsMaintainer(first: 100, after: $after, activeOnly: true) {
        pageInfo { hasNextPage endCursor }
        nodes {
          sponsorEntity {
            __typename
            ... on User { login name websiteUrl }
            ... on Organization { login name websiteUrl }
          }
        }
      }
    }
    ... on Organization {
      sponsorshipsAsMaintainer(first: 100, after: $after, activeOnly: true) {
        pageInfo { hasNextPage endCursor }
        nodes {
          sponsorEntity {
            __typename
            ... on User { login name websiteUrl }
            ... on Organization { login name websiteUrl }
          }
        }
      }
    }
  }
}
"""

OC_BACKERS_QUERY = """
query($slug: String!, $limit: Int!, $offset: Int!) {
  collective(slug: $slug) {
    name
    members(role: BACKER, limit: $limit, offset: $offset) {
      totalCount
      nodes {
        since
        totalDonations { value currency }
        account { name slug website type }
      }
    }
  }
}
"""

LEAD_FIELDS = [
    "company", "domain", "evidence_url", "fit_score", "fit_notes",
    "contact_name", "contact_role", "contact_url", "contact_email", "status",
]

COMPANY_FIELDS = [
    "rank", "company", "entity_type", "score", "repos_count", "sources",
    "domain", "github_login", "oc_slug", "total_donated", "first_seen",
    "max_repo_stars", "source_repos", "links", "evidence_urls",
]


class SponsorMinerError(RuntimeError):
    pass


class GitHubError(SponsorMinerError):
    pass


class NotFoundError(GitHubError):
    pass


class GitHubRateLimitError(GitHubError):
    pass


@dataclass
class SeedRepo:
    repo: str
    stars: int
    owner: str
    homepage: str
    origin: str


@dataclass
class SponsorEdge:
    name: str
    name_quality: int  # 2 = API-provided, 1 = link text, 0 = derived from domain
    domain: str
    github_login: str
    oc_slug: str
    entity_type: str  # organization | individual | unknown
    source: str  # readme | sponsor_svg | github_sponsors | open_collective
    source_repo: str
    source_repo_stars: int
    evidence_url: str
    link_url: str
    since: str
    total_donated: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "github_login": self.github_login,
            "oc_slug": self.oc_slug,
            "entity_type": self.entity_type,
            "source": self.source,
            "source_repo": self.source_repo,
            "evidence_url": self.evidence_url,
            "link_url": self.link_url,
            "since": self.since,
            "total_donated": self.total_donated,
        }


@dataclass
class Company:
    name: str = ""
    name_quality: int = -1
    domain: str = ""
    github_login: str = ""
    oc_slug: str = ""
    org_signal: bool = False
    individual_signal: bool = False
    repos: set = field(default_factory=set)
    sources: set = field(default_factory=set)
    evidence_urls: list = field(default_factory=list)
    links: set = field(default_factory=set)
    total_donated: float = 0.0
    first_seen: str = ""
    max_repo_stars: int = 0
    score: int = 0

    @property
    def entity_type(self) -> str:
        # "unknown" means no typed source (GraphQL profile / Open Collective)
        # confirmed the entity yet — a domain alone can be a personal site.
        if self.org_signal:
            return "organization"
        if self.individual_signal:
            return "individual"
        return "unknown"


@dataclass
class ClassifiedUrls:
    company_links: list = field(default_factory=list)
    github_logins: list = field(default_factory=list)
    sponsor_pages: list = field(default_factory=list)
    oc_slugs: list = field(default_factory=list)
    svg_urls: list = field(default_factory=list)


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def search_repositories(self, query: str, limit: int) -> list[dict[str, Any]]:
        payload = self.request_json(
            "/search/repositories",
            params=[("q", query), ("sort", "stars"), ("order", "desc"), ("per_page", str(min(100, limit)))],
        )
        return list(payload.get("items") or [])[:limit]

    def fetch_repo(self, repo: str) -> dict[str, Any]:
        return self.request_json(f"/repos/{repo}")

    def fetch_readme(self, repo: str) -> tuple[str, str]:
        """Return (path, text) of the repo's README."""
        payload = self.request_json(f"/repos/{repo}/readme")
        path = str(payload.get("path") or "README.md")
        content = payload.get("content") or ""
        if content:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        else:
            text = self.request_text(f"/repos/{repo}/readme", accept="application/vnd.github.raw")
        return path, text

    def fetch_file(self, repo: str, path: str) -> str:
        return self.request_text(f"/repos/{repo}/contents/{path}", accept="application/vnd.github.raw")

    def fetch_public_sponsors(self, login: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        for page in range(MAX_SPONSOR_PAGES):
            data = self.graphql(SPONSORS_QUERY, {"login": login, "after": after})
            owner = (data or {}).get("repositoryOwner") or {}
            connection = owner.get("sponsorshipsAsMaintainer") or {}
            nodes.extend(node for node in connection.get("nodes") or [] if node)
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            time.sleep(GRAPHQL_SLEEP)
        return nodes

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode()
        payload = json.loads(self.request("/graphql", data=body).decode())
        if payload.get("data") is None:
            raise GitHubError(f"GraphQL query failed: {str(payload.get('errors'))[:300]}")
        return payload["data"]

    def request_json(self, path: str, params: list[tuple[str, str]] | None = None,
                     accept: str = "application/vnd.github+json") -> dict[str, Any]:
        return json.loads(self.request(path, params=params, accept=accept).decode())

    def request_text(self, path: str, params: list[tuple[str, str]] | None = None,
                     accept: str = "application/vnd.github+json") -> str:
        return self.request(path, params=params, accept=accept).decode("utf-8", errors="replace")

    def request(self, path: str, params: list[tuple[str, str]] | None = None,
                accept: str = "application/vnd.github+json", data: bytes | None = None) -> bytes:
        query = f"?{urlencode(params)}" if params else ""
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"{GITHUB_API}{path}{query}", headers=headers, data=data)
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            remaining = error.headers.get("x-ratelimit-remaining", "") if error.headers else ""
            if error.code in {403, 429} and remaining == "0":
                raise GitHubRateLimitError("GitHub API rate limit exceeded") from error
            if error.code == 404:
                raise NotFoundError(f"GitHub API 404: {path}") from error
            raise GitHubError(f"GitHub API request failed: {error.code} {error.reason} ({path})") from error
        except (OSError, URLError) as error:
            raise GitHubError(f"GitHub API request failed: {error} ({path})") from error


def oc_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = Request(
        OPEN_COLLECTIVE_API,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
            break
        except HTTPError as error:
            if error.code == 429 and attempt == 0:
                time.sleep(30)
                continue
            raise SponsorMinerError(f"Open Collective API request failed: {error}") from error
        except (OSError, URLError) as error:
            raise SponsorMinerError(f"Open Collective API request failed: {error}") from error
    if payload.get("data") is None:
        raise SponsorMinerError(f"Open Collective query failed: {str(payload.get('errors'))[:300]}")
    return payload["data"]


def fetch_oc_backers(slug: str) -> list[dict[str, Any]]:
    """Return org backers of a collective; [] when the slug is not a collective
    (README sponsor sections often link individual OC profiles)."""
    backers: list[dict[str, Any]] = []
    for page in range(MAX_OC_PAGES):
        data = oc_graphql(OC_BACKERS_QUERY, {"slug": slug, "limit": 100, "offset": page * 100})
        collective = data.get("collective")
        if not collective:
            break
        nodes = (collective.get("members") or {}).get("nodes") or []
        backers.extend(node for node in nodes if node)
        if len(nodes) < 100:
            break
        time.sleep(OC_SLEEP)
    return backers


def fetch_url(url: str, max_bytes: int = SVG_MAX_BYTES) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read(max_bytes).decode("utf-8", errors="replace")
    except (OSError, URLError, HTTPError) as error:
        raise SponsorMinerError(f"fetch failed for {url}: {error}") from error


def get_gh_token() -> str:
    try:
        completed = subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.SubprocessError) as error:
        details = getattr(error, "stderr", "") or str(error)
        raise GitHubError(f"gh auth token failed: {details}") from error
    token = completed.stdout.strip()
    if not token:
        raise GitHubError("gh auth token returned an empty token")
    return token


def parse_github_repo_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        raise ValueError("target must be a GitHub repo URL like https://github.com/owner/repo")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("target must include both owner and repo")
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{parts[0]}/{repo}"


def unique_session_dir(root: Path, session_name: str) -> Path:
    base = f"{sanitize_session_name(session_name)}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    candidate = root / base
    index = 2
    while candidate.exists():
        candidate = root / f"{base}-{index}"
        index += 1
    return candidate


def sanitize_session_name(name: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in name.strip())
    safe = safe.strip("-")
    return safe if safe not in {"", ".", ".."} else "target"


def sanitize_repo_filename(repo: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", repo.replace("/", "__"))
    return f"{safe}.md"


def domain_of(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    domain = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    return domain[4:] if domain.startswith("www.") else domain


def external_domain(url: str) -> str:
    """Domain usable as a company identity — never a GitHub URL, which some
    profiles set as their website and would wrongly merge unrelated sponsors."""
    domain = domain_of(url)
    return "" if domain in {"github.com", "gist.github.com"} else domain


def clean_url(url: str) -> str:
    parsed = urlparse(url)
    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query)
        if not key.lower().startswith("utm_") and key.lower() not in {"ref", "ref_src", "source"}
    ]
    return urlunparse(parsed._replace(query=urlencode(params), fragment=""))


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def company_name_from_domain(domain: str) -> str:
    if not domain:
        return ""
    parts = domain.split(".")
    name = parts[0]
    if name in {"app", "blog", "docs", "get", "go", "login", "m", "try", "use", "www", "www2"} and len(parts) > 1:
        name = parts[1]
    return name.replace("-", " ").title()


def clean_link_text(text: str) -> str:
    cleaned = " ".join(re.sub(r"[*_`~|]", "", text).split()).strip()
    if not cleaned or cleaned.startswith(("http://", "https://", "!")) or len(cleaned) > 60:
        return ""
    return cleaned


def sponsor_ranges(lines: list[str]) -> list[tuple[int, int]]:
    ranges: list[list[int]] = []
    headings = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
    for position, (line_no, level, title) in enumerate(headings):
        if not SPONSOR_HEADING_RE.search(title):
            continue
        end = len(lines)
        for next_line_no, next_level, _ in headings[position + 1:]:
            if next_level <= level:
                end = next_line_no
                break
        ranges.append([line_no, min(end, line_no + 150)])

    covered: set[int] = set()
    for start, end in ranges:
        covered.update(range(start, end))
    for index, line in enumerate(lines):
        if index in covered:
            continue
        window: tuple[int, int] | None = None
        if HTML_SPONSOR_HEADING_RE.search(line):
            window = (index, min(len(lines), index + 60))
        elif SPONSOR_LINE_RE.search(line):
            window = (max(0, index - 2), min(len(lines), index + 10))
        if window:
            ranges.append(list(window))
            covered.update(range(*window))

    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def classify_urls(urls: list[str], extra_ignored: set[str] | frozenset = frozenset()) -> ClassifiedUrls:
    result = ClassifiedUrls()

    def add(bucket: list, value: str) -> None:
        if value.lower() not in (item.lower() for item in bucket):
            bucket.append(value)

    for raw in urls:
        url = clean_url(raw)
        domain = domain_of(url)
        if not domain:
            continue
        lower = url.lower()
        parts = [part for part in urlparse(url).path.split("/") if part]
        if domain == "github.com":
            if len(parts) >= 2 and parts[0].lower() == "sponsors" and GITHUB_LOGIN_RE.fullmatch(parts[1]):
                add(result.sponsor_pages, parts[1])
            elif len(parts) == 1 and GITHUB_LOGIN_RE.fullmatch(parts[0]) and parts[0].lower() not in RESERVED_GITHUB_PATHS:
                add(result.github_logins, parts[0])
            continue
        if domain == "opencollective.com":
            if parts and OC_SLUG_RE.fullmatch(parts[0]) and parts[0] not in RESERVED_OC_PATHS:
                add(result.oc_slugs, parts[0])
            continue
        filename = lower.rsplit("/", 1)[-1]
        if ".svg" in filename:
            if "sponsor" in lower:
                add(result.svg_urls, url)
            continue
        if lower.endswith(IMAGE_EXTENSIONS):
            continue
        if domain in IGNORED_DOMAINS or domain in extra_ignored or domain.endswith(".github.io"):
            continue
        add(result.company_links, url)
    return result


def parse_funding_yaml(text: str) -> tuple[list[str], list[str]]:
    """Return (github_logins, open_collective_slugs) from a FUNDING.yml."""
    github_logins: list[str] = []
    oc_slugs: list[str] = []
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        item_match = re.match(r"^\s+-\s*(.+)$", line)
        if item_match and current_key:
            value = item_match.group(1).strip().strip("'\"")
            if current_key == "github":
                github_logins.append(value)
            elif current_key == "open_collective":
                oc_slugs.append(value)
            continue
        key_match = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if not key_match:
            continue
        current_key = key_match.group(1)
        value = key_match.group(2).strip()
        if not value:
            continue
        if value.startswith("["):
            values = [item.strip().strip("'\"") for item in value.strip("[]").split(",") if item.strip()]
        else:
            values = [value.strip("'\"")]
        if current_key == "github":
            github_logins.extend(values)
        elif current_key == "open_collective":
            oc_slugs.extend(values)
        current_key = ""
    return github_logins, oc_slugs


def register_target(mapping: dict[str, SeedRepo], key: str, seed: SeedRepo) -> None:
    existing = mapping.get(key)
    if existing is None or seed.stars > existing.stars:
        mapping[key] = seed


def oc_slug_matches_seed(slug: str, seed: SeedRepo) -> bool:
    """README backer sections link individual OC profiles by the hundreds;
    only trust a README-derived slug that names the project or its owner."""
    normalized = re.sub(r"[^a-z0-9]", "", slug.lower())
    owner, _, name = seed.repo.partition("/")
    return normalized in {re.sub(r"[^a-z0-9]", "", part.lower()) for part in (owner, name)}


def pick_topics(args: argparse.Namespace, target: dict[str, Any]) -> list[str]:
    if args.topics.strip().lower() == "none":
        return []
    if args.topics.strip():
        return [topic.strip() for topic in args.topics.split(",") if topic.strip()]
    target_topics = list(target.get("topics") or [])[:6]
    return target_topics or FALLBACK_TOPICS


def build_seed_list(gh: GitHubClient, args: argparse.Namespace, topics: list[str],
                    target_repo: str, errors: list[str]) -> list[SeedRepo]:
    seeds: dict[str, SeedRepo] = {}

    explicit: list[str] = [seed.strip() for seed in args.seeds.split(",") if seed.strip()]
    if args.seed_file:
        explicit.extend(
            line.strip()
            for line in Path(args.seed_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    for repo in explicit:
        if repo.lower() == target_repo.lower() or repo in seeds:
            continue
        try:
            info = gh.fetch_repo(repo)
        except GitHubError as error:
            errors.append(f"explicit seed failed for {repo}: {error}")
            continue
        seeds[str(info.get("full_name") or repo)] = SeedRepo(
            repo=str(info.get("full_name") or repo),
            stars=int(info.get("stargazers_count") or 0),
            owner=str((info.get("owner") or {}).get("login") or repo.split("/")[0]),
            homepage=str(info.get("homepage") or ""),
            origin="explicit",
        )
        time.sleep(REST_SLEEP)

    if topics:
        per_topic = max(10, -(-args.max_seed_repos // len(topics)))
        for topic in topics:
            try:
                items = gh.search_repositories(f"topic:{topic} stars:>={args.min_stars} archived:false", per_topic)
            except GitHubError as error:
                errors.append(f"topic search failed for {topic}: {error}")
                continue
            for item in items:
                full_name = str(item.get("full_name") or "")
                if not full_name or full_name.lower() == target_repo.lower() or full_name in seeds:
                    continue
                seeds[full_name] = SeedRepo(
                    repo=full_name,
                    stars=int(item.get("stargazers_count") or 0),
                    owner=str((item.get("owner") or {}).get("login") or full_name.split("/")[0]),
                    homepage=str(item.get("homepage") or ""),
                    origin=f"topic:{topic}",
                )
            time.sleep(SEARCH_SLEEP)

    explicit_seeds = [seed for seed in seeds.values() if seed.origin == "explicit"]
    topic_seeds = sorted(
        (seed for seed in seeds.values() if seed.origin != "explicit"),
        key=lambda seed: -seed.stars,
    )
    keep = max(0, args.max_seed_repos - len(explicit_seeds))
    return explicit_seeds + topic_seeds[:keep]


def fetch_target_context(gh: GitHubClient, target_repo: str, errors: list[str]) -> dict[str, Any]:
    try:
        repo = gh.fetch_repo(target_repo)
    except GitHubError as error:
        errors.append(f"target repo metadata failed for {target_repo}: {error}")
        return {"repo": target_repo, "topics": [], "owner": target_repo.split("/")[0], "homepage": ""}
    return {
        "repo": target_repo,
        "description": repo.get("description") or "",
        "homepage": repo.get("homepage") or "",
        "stars": repo.get("stargazers_count") or 0,
        "topics": repo.get("topics") or [],
        "language": repo.get("language") or "",
        "owner": str((repo.get("owner") or {}).get("login") or target_repo.split("/")[0]),
    }


def make_readme_edge(url: str, link_texts: dict[str, str], source: str, seed: SeedRepo, evidence: str) -> SponsorEdge:
    text = clean_link_text(link_texts.get(url, ""))
    domain = domain_of(url)
    return SponsorEdge(
        name=text or company_name_from_domain(domain),
        name_quality=1 if text else 0,
        domain=domain,
        github_login="",
        oc_slug="",
        entity_type="unknown",
        source=source,
        source_repo=seed.repo,
        source_repo_stars=seed.stars,
        evidence_url=evidence,
        link_url=url,
        since="",
        total_donated=0.0,
    )


def make_login_edge(login: str, source: str, seed: SeedRepo, evidence: str) -> SponsorEdge:
    return SponsorEdge(
        name=login,
        name_quality=0,
        domain="",
        github_login=login,
        oc_slug="",
        entity_type="unknown",
        source=source,
        source_repo=seed.repo,
        source_repo_stars=seed.stars,
        evidence_url=evidence,
        link_url=f"https://github.com/{login}",
        since="",
        total_donated=0.0,
    )


def mine_seed_repos(
    gh: GitHubClient,
    seeds: list[SeedRepo],
    session: Path,
    errors: list[str],
) -> tuple[list[SponsorEdge], dict[str, SeedRepo], dict[str, SeedRepo], int]:
    readmes_dir = session / "readmes"
    readmes_dir.mkdir()
    edges: list[SponsorEdge] = []
    graphql_targets: dict[str, SeedRepo] = {}
    oc_targets: dict[str, SeedRepo] = {}
    readmes_fetched = 0

    for seed in seeds:
        register_target(graphql_targets, seed.owner, seed)
        repo_edges = 0
        login_edges = 0
        try:
            readme_path, readme = gh.fetch_readme(seed.repo)
        except NotFoundError:
            readme_path, readme = "README.md", ""
        except GitHubError as error:
            errors.append(f"README fetch failed for {seed.repo}: {error}")
            readme_path, readme = "README.md", ""
        if readme:
            (readmes_dir / sanitize_repo_filename(seed.repo)).write_text(readme, encoding="utf-8")
            readmes_fetched += 1

        extra_ignored = {domain_of(seed.homepage)} - {""}
        svg_queue: list[tuple[str, str]] = []
        lines = readme.splitlines()
        for start, end in sponsor_ranges(lines):
            block = "\n".join(lines[start:end])
            link_texts = {
                clean_url(url): text
                for text, url in MD_LINK_RE.findall(block)
                if "![" not in text
            }
            classified = classify_urls(extract_urls(block), extra_ignored)
            evidence = f"https://github.com/{seed.repo}/blob/HEAD/{readme_path}#L{start + 1}"
            for url in classified.company_links:
                if repo_edges >= MAX_EDGES_PER_REPO:
                    break
                edges.append(make_readme_edge(url, link_texts, "readme", seed, evidence))
                repo_edges += 1
            for login in classified.github_logins:
                if repo_edges >= MAX_EDGES_PER_REPO or login_edges >= MAX_GITHUB_LOGINS_PER_REPO:
                    break
                edges.append(make_login_edge(login, "readme", seed, evidence))
                repo_edges += 1
                login_edges += 1
            for login in classified.sponsor_pages:
                register_target(graphql_targets, login, seed)
            for slug in classified.oc_slugs:
                if oc_slug_matches_seed(slug, seed):
                    register_target(oc_targets, slug, seed)
            svg_queue.extend((url, evidence) for url in classified.svg_urls)

        for svg_url, _ in svg_queue[:MAX_SVGS_PER_REPO]:
            try:
                svg_text = fetch_url(svg_url)
            except SponsorMinerError as error:
                errors.append(f"sponsor SVG fetch failed for {seed.repo}: {error}")
                continue
            classified = classify_urls(SVG_HREF_RE.findall(svg_text), extra_ignored)
            link_texts: dict[str, str] = {}
            for url in classified.company_links:
                if repo_edges >= MAX_EDGES_PER_REPO:
                    break
                edges.append(make_readme_edge(url, link_texts, "sponsor_svg", seed, svg_url))
                repo_edges += 1
            for login in classified.github_logins:
                if repo_edges >= MAX_EDGES_PER_REPO or login_edges >= MAX_GITHUB_LOGINS_PER_REPO:
                    break
                edges.append(make_login_edge(login, "sponsor_svg", seed, svg_url))
                repo_edges += 1
                login_edges += 1
            for login in classified.sponsor_pages:
                register_target(graphql_targets, login, seed)
            for slug in classified.oc_slugs:
                if oc_slug_matches_seed(slug, seed):
                    register_target(oc_targets, slug, seed)

        try:
            funding = gh.fetch_file(seed.repo, ".github/FUNDING.yml")
        except NotFoundError:
            funding = ""
        except GitHubError as error:
            errors.append(f"FUNDING.yml fetch failed for {seed.repo}: {error}")
            funding = ""
        if funding:
            funding_logins, funding_slugs = parse_funding_yaml(funding)
            for login in funding_logins:
                if GITHUB_LOGIN_RE.fullmatch(login):
                    register_target(graphql_targets, login, seed)
            for slug in funding_slugs:
                if OC_SLUG_RE.fullmatch(slug.lower()):
                    register_target(oc_targets, slug, seed)

        time.sleep(REST_SLEEP)

    return edges, graphql_targets, oc_targets, readmes_fetched


def mine_github_sponsors(gh: GitHubClient, graphql_targets: dict[str, SeedRepo],
                         errors: list[str]) -> list[SponsorEdge]:
    edges: list[SponsorEdge] = []
    owners = sorted(graphql_targets.items(), key=lambda item: -item[1].stars)[:GRAPHQL_OWNER_CAP]
    for login, seed in owners:
        try:
            sponsorships = gh.fetch_public_sponsors(login)
        except GitHubError as error:
            errors.append(f"GitHub Sponsors query failed for {login}: {error}")
            continue
        for sponsorship in sponsorships:
            entity = sponsorship.get("sponsorEntity") or {}
            sponsor_login = entity.get("login")
            if not sponsor_login:
                continue
            website = str(entity.get("websiteUrl") or "")
            edges.append(SponsorEdge(
                name=str(entity.get("name") or sponsor_login),
                name_quality=2 if entity.get("name") else 0,
                domain=external_domain(website),
                github_login=str(sponsor_login),
                oc_slug="",
                entity_type="organization" if entity.get("__typename") == "Organization" else "individual",
                source="github_sponsors",
                source_repo=seed.repo,
                source_repo_stars=seed.stars,
                evidence_url=f"https://github.com/sponsors/{login}",
                link_url=website or f"https://github.com/{sponsor_login}",
                since="",
                total_donated=0.0,
            ))
        time.sleep(GRAPHQL_SLEEP)
    return edges


def mine_open_collective(oc_targets: dict[str, SeedRepo], errors: list[str]) -> list[SponsorEdge]:
    edges: list[SponsorEdge] = []
    for slug, seed in oc_targets.items():
        try:
            backers = fetch_oc_backers(slug)
        except SponsorMinerError as error:
            errors.append(f"Open Collective query failed for {slug}: {error}")
            continue
        for backer in backers:
            account = backer.get("account") or {}
            if account.get("type") != "ORGANIZATION":
                continue
            donations = backer.get("totalDonations") or {}
            total = float(donations.get("value") or 0.0)
            if total < OC_MIN_TOTAL:
                continue
            website = str(account.get("website") or "")
            edges.append(SponsorEdge(
                name=str(account.get("name") or account.get("slug") or ""),
                name_quality=2 if account.get("name") else 0,
                domain=domain_of(website),
                github_login="",
                oc_slug=str(account.get("slug") or ""),
                entity_type="organization",
                source="open_collective",
                source_repo=seed.repo,
                source_repo_stars=seed.stars,
                evidence_url=f"https://opencollective.com/{slug}",
                link_url=website or f"https://opencollective.com/{account.get('slug') or slug}",
                since=str(backer.get("since") or ""),
                total_donated=total,
            ))
        time.sleep(OC_SLEEP)
    return edges


def resolve_github_profiles(gh: GitHubClient, edges: list[SponsorEdge], errors: list[str]) -> int:
    counts = Counter(edge.github_login for edge in edges if edge.github_login and not edge.domain)
    profiles: dict[str, dict[str, Any]] = {}
    for login, _ in counts.most_common(PROFILE_RESOLVE_CAP):
        try:
            profiles[login] = gh.request_json(f"/users/{login}")
        except GitHubError:
            continue
        time.sleep(REST_SLEEP)
    for edge in edges:
        profile = profiles.get(edge.github_login)
        if not profile:
            continue
        if not edge.domain:
            edge.domain = external_domain(str(profile.get("blog") or ""))
        if profile.get("name") and edge.name_quality < 2:
            edge.name = str(profile["name"])
            edge.name_quality = 2
        edge.entity_type = "organization" if profile.get("type") == "Organization" else "individual"
    return len(profiles)


def company_key(edge: SponsorEdge) -> str:
    if edge.domain:
        return f"domain:{edge.domain}"
    if edge.github_login:
        return f"github:{edge.github_login.lower()}"
    if edge.oc_slug:
        return f"oc:{edge.oc_slug.lower()}"
    return f"name:{edge.name.lower()}"


def aggregate_companies(edges: list[SponsorEdge], exclude_domains: set[str],
                        exclude_logins: set[str]) -> list[Company]:
    companies: dict[str, Company] = {}
    for edge in edges:
        if edge.domain and edge.domain in exclude_domains:
            continue
        if edge.github_login and edge.github_login.lower() in exclude_logins:
            continue
        company = companies.setdefault(company_key(edge), Company())
        if edge.name and edge.name_quality > company.name_quality:
            company.name = edge.name
            company.name_quality = edge.name_quality
        company.domain = company.domain or edge.domain
        company.github_login = company.github_login or edge.github_login
        company.oc_slug = company.oc_slug or edge.oc_slug
        if edge.entity_type == "organization":
            company.org_signal = True
        elif edge.entity_type == "individual":
            company.individual_signal = True
        company.repos.add(edge.source_repo)
        company.sources.add(edge.source)
        if edge.evidence_url and edge.evidence_url not in company.evidence_urls:
            company.evidence_urls.append(edge.evidence_url)
        if edge.link_url:
            company.links.add(edge.link_url)
        company.total_donated += edge.total_donated
        if edge.since and (not company.first_seen or edge.since < company.first_seen):
            company.first_seen = edge.since
        company.max_repo_stars = max(company.max_repo_stars, edge.source_repo_stars)
    result = list(companies.values())
    for company in result:
        company.score = score_company(company)
    result.sort(key=lambda company: (-company.score, -len(company.repos), company.name.lower()))
    return result


def score_company(company: Company) -> int:
    score = 3 * len(company.repos)
    if {"github_sponsors", "open_collective"} & company.sources:
        score += 2
    if company.entity_type == "organization":
        score += 2
    elif company.entity_type == "individual":
        score -= 3
    if company.total_donated >= 5000:
        score += 3
    elif company.total_donated >= 1000:
        score += 2
    elif company.total_donated >= 100:
        score += 1
    if company.max_repo_stars >= 5000:
        score += 1
    return score


def write_seed_csv(path: Path, seeds: list[SeedRepo]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["repo", "stars", "origin", "homepage"])
        for seed in seeds:
            writer.writerow([seed.repo, seed.stars, seed.origin, seed.homepage])


def write_edges_jsonl(path: Path, edges: list[SponsorEdge]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for edge in edges:
            file.write(json.dumps(edge.to_dict(), sort_keys=True) + "\n")


def write_companies_csv(path: Path, companies: list[Company]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COMPANY_FIELDS)
        writer.writeheader()
        for rank, company in enumerate(companies, start=1):
            writer.writerow({
                "rank": rank,
                "company": company.name or company_name_from_domain(company.domain) or company.github_login,
                "entity_type": company.entity_type,
                "score": company.score,
                "repos_count": len(company.repos),
                "sources": "; ".join(sorted(company.sources)),
                "domain": company.domain,
                "github_login": company.github_login,
                "oc_slug": company.oc_slug,
                "total_donated": f"{company.total_donated:.0f}" if company.total_donated else "",
                "first_seen": company.first_seen[:10],
                "max_repo_stars": company.max_repo_stars,
                "source_repos": "; ".join(sorted(company.repos)),
                "links": "; ".join(sorted(company.links)[:3]),
                "evidence_urls": "; ".join(company.evidence_urls[:3]),
            })


def write_verified_leads_template(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        csv.DictWriter(file, fieldnames=LEAD_FIELDS).writeheader()


def write_run_summary(path: Path, summary: dict[str, Any]) -> None:
    target = summary["target"]
    source_counts = summary["source_counts"]
    lines = [
        "# Sponsor Miner Run Summary",
        "",
        f"- Target repo: {summary['target_repo']} ({target.get('stars', '?')} stars)",
        f"- Target description: {target.get('description', '')}",
        f"- Topics used: {', '.join(summary['topics']) or 'none'}",
        f"- Seed repos mined: {summary['seed_count']} (READMEs fetched: {summary['readmes_fetched']})",
        f"- GitHub Sponsors accounts queried: {summary['graphql_target_count']}",
        f"- Open Collective collectives queried: {summary['oc_target_count']}",
        f"- GitHub profiles resolved: {summary['profiles_resolved']}",
        "- Sponsorship edges by source: "
        + ", ".join(f"{source}={count}" for source, count in sorted(source_counts.items())),
        "- Companies ranked: "
        + f"{summary['company_count']} ("
        + ", ".join(f"{kind}={count}" for kind, count in sorted(summary["type_counts"].items()))
        + ")",
        "",
        "## Top companies",
        "",
    ]
    for company in summary["top_companies"]:
        lines.append(
            f"- {company.name or company.domain or company.github_login} "
            f"(score {company.score}, repos {len(company.repos)}, "
            f"sources {'/'.join(sorted(company.sources))})"
        )
    lines.extend([
        "",
        "Next: verify rows in `companies-ranked.csv` top-down and fill `verified-leads.csv`.",
    ])
    if summary["errors"]:
        lines.extend(["", "## Non-fatal errors", ""])
        lines.extend(f"- {error}" for error in summary["errors"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mine sponsor leads for a target GitHub repo from peer repos' sponsor data.",
        epilog=(
            "example: sponsor_miner.py https://github.com/owner/repo "
            "--topics ai-agents,developer-tools --seeds vitejs/vite,antfu/unocss"
        ),
    )
    parser.add_argument("target", help="GitHub URL of the repo you are seeking sponsors for")
    parser.add_argument("--topics", default="",
                        help="comma-separated GitHub topics for seed search; "
                             "defaults to the target repo's own topics; 'none' disables topic search")
    parser.add_argument("--seeds", default="", help="comma-separated owner/repo seeds to always include")
    parser.add_argument("--seed-file", default="", help="file with one owner/repo seed per line")
    parser.add_argument("--max-seed-repos", type=int, default=40, help="cap on seed repos (default 40)")
    parser.add_argument("--min-stars", type=int, default=300, help="star floor for topic-search seeds (default 300)")
    parser.add_argument("--out-root", default="", help="output root (default ./sponsor-hunt)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        target_repo = parse_github_repo_url(args.target)
        token = get_gh_token()
    except (SponsorMinerError, ValueError) as error:
        print(f"sponsor-miner: {error}", file=sys.stderr)
        return 1
    gh = GitHubClient(token)
    out_root = Path(args.out_root) if args.out_root else Path.cwd() / "sponsor-hunt"
    session = unique_session_dir(out_root, target_repo.replace("/", "__"))

    errors: list[str] = []
    try:
        session.mkdir(parents=True, exist_ok=False)
        target = fetch_target_context(gh, target_repo, errors)
        topics = pick_topics(args, target)
        seeds = build_seed_list(gh, args, topics, target_repo, errors)
        if not seeds:
            print("sponsor-miner: no seed repos found; pass --seeds or --topics", file=sys.stderr)
            return 1
        write_seed_csv(session / "seed-repos.csv", seeds)
        print(f"Mining {len(seeds)} seed repos (topics: {', '.join(topics) or 'none'})")

        edges, graphql_targets, oc_targets, readmes_fetched = mine_seed_repos(gh, seeds, session, errors)
        edges.extend(mine_github_sponsors(gh, graphql_targets, errors))
        edges.extend(mine_open_collective(oc_targets, errors))
        profiles_resolved = resolve_github_profiles(gh, edges, errors)

        exclude_domains = {domain_of(str(target.get("homepage") or ""))} - {""}
        exclude_logins = {str(target.get("owner") or "").lower()}
        companies = aggregate_companies(edges, exclude_domains, exclude_logins)

        write_edges_jsonl(session / "sponsor-edges.jsonl", edges)
        write_companies_csv(session / "companies-ranked.csv", companies)
        write_verified_leads_template(session / "verified-leads.csv")
        type_counts = Counter(company.entity_type for company in companies)
        organization_count = type_counts.get("organization", 0)
        summary = {
            "target_repo": target_repo,
            "target": target,
            "topics": topics,
            "seed_count": len(seeds),
            "readmes_fetched": readmes_fetched,
            "graphql_target_count": min(len(graphql_targets), GRAPHQL_OWNER_CAP),
            "oc_target_count": len(oc_targets),
            "profiles_resolved": profiles_resolved,
            "source_counts": dict(Counter(edge.source for edge in edges)),
            "company_count": len(companies),
            "type_counts": dict(type_counts),
            "top_companies": [company for company in companies if company.entity_type != "individual"][:15],
            "errors": errors,
        }
        write_run_summary(session / "run-summary.md", summary)
    except SponsorMinerError as error:
        print(f"sponsor-miner: {error}", file=sys.stderr)
        return 1

    print(f"Wrote {len(companies)} companies ({organization_count} organization-type) to {session / 'companies-ranked.csv'}")
    if errors:
        print(f"Completed with {len(errors)} non-fatal errors. See {session / 'run-summary.md'}.", file=sys.stderr)
    print(f"SESSION_PATH={session}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

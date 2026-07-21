"""Diff instrument: read-only local git diff viewer (see repodiff.py)."""
from fastapi import APIRouter, HTTPException

from flightdeck import repodiff

router = APIRouter(tags=["diff"])


def _rd(fn, *a):
    try:
        return fn(*a)
    except repodiff.GitError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/repo/list")
def repo_list():
    return _rd(repodiff.list_repos)


@router.get("/api/repo/refs")
def repo_refs(repo: str):
    return _rd(repodiff.list_refs, repo)


@router.get("/api/repo/compare")
def repo_compare(repo: str, base: str, head: str):
    return _rd(repodiff.compare, repo, base, head)


@router.get("/api/repo/filediff")
def repo_filediff(repo: str, base: str, head: str, path: str):
    return _rd(repodiff.file_diff, repo, base, head, path)


@router.post("/api/repo/fetch")
def repo_fetch(body: dict):
    return _rd(repodiff.do_fetch, body.get("repo"))


@router.post("/api/repo/pull")
def repo_pull(body: dict):
    return _rd(repodiff.do_pull, body.get("repo"))

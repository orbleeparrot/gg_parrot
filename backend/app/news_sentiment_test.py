"""Admin observation page; opening/closing it cannot schedule inference."""
from fastapi import APIRouter, Depends, Query
from .auth import current_user, require_admin
from . import news_sentiment


def require_test_admin(user=Depends(current_user)):
    return require_admin(user)


router = APIRouter(prefix="/api/admin/news-test", dependencies=[Depends(require_test_admin)])


@router.get("/results")
def results(after: str = Query(default="", max_length=40)):
    data = news_sentiment.snapshot()
    if after and after == data["version"]:
        return {"unchanged": True, "version": data["version"]}
    return data

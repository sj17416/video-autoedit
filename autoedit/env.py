""".env 읽기 + API 키 정리 / 확인."""
import os

KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
# 사람들이 흔히 잘못 적는 이름도 받아준다
ALIASES = {"OPENAI_KEY": "OPENAI_API_KEY", "OPENAI_APIKEY": "OPENAI_API_KEY", "ANTHROPIC_KEY": "ANTHROPIC_API_KEY",
           "CLAUDE_API_KEY": "ANTHROPIC_API_KEY", "CLAUDE_KEY": "ANTHROPIC_API_KEY"}


def clean(value):
    return (value or "").strip().strip('"').strip("'").strip()


def load_env(root):
    from dotenv import load_dotenv
    load_dotenv(root / ".env")
    for alias, real in ALIASES.items():
        if not os.environ.get(real) and os.environ.get(alias):
            os.environ[real] = os.environ[alias]
    for name in KEYS.values():
        if name in os.environ:
            os.environ[name] = clean(os.environ[name])


def check_keys():
    """실제로 API에 물어봐서 키가 맞는지 확인 (비용 없음: 모델 목록 조회)."""
    result = {}
    if os.environ.get(KEYS["anthropic"]):
        try:
            import anthropic
            anthropic.Anthropic().models.list(limit=1)
            result["anthropic"] = "ok"
        except Exception as e:
            result["anthropic"] = _reason(e)
    if os.environ.get(KEYS["openai"]):
        try:
            import openai
            openai.OpenAI().models.list()
            result["openai"] = "ok"
        except Exception as e:
            result["openai"] = _reason(e)
    return result


def _reason(e):
    name = type(e).__name__
    if "Authentication" in name:
        return "키가 틀렸습니다 (복사할 때 일부가 빠졌거나, 삭제된 키일 수 있어요)"
    if "PermissionDenied" in name:
        return "권한이 없습니다 (계정/조직 설정 확인 필요)"
    if "Connection" in name:
        return "인터넷 연결을 확인할 수 없습니다"
    return f"확인 실패: {name}"

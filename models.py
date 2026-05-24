"""数据模型和共享配置常量"""

from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

IMAGE_DIMENSION_MIN_SIDE = 480
IMAGE_ASPECT_RATIO_MAX = 5.0
IMAGE_DIMENSION_BYTE_CAP = 512 * 1024
IMAGE_DIMENSION_WORKERS = 8
IMAGE_DIMENSION_TIMEOUT = (3.05, 3)
IMAGE_DIMENSION_FAIL_OPEN = False

CAPTCHA_PATTERNS = (
    "百度安全验证",
    "安全验证",
    "请完成下方验证",
    "验证码",
    "captcha",
    "anti-bot",
    "人机验证",
    "byted_acrawler",
    "__ac_signature",
    "__ac_nonce",
)

@dataclass
class Article:
    title: str = ""
    subtitle: str = ""
    author: str = ""
    source_url: str = ""
    markdown: str = ""
    images: list[str] = field(default_factory=list)

"""非敏感默认配置。API 密钥只从运行时输入或环境变量读取。"""

# 主生成服务（提取 + 翻译）
DEFAULT_API_BASE = "https://api.vectorengine.ai/v1"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_PROFILE = "yanyun"

# 辅助服务模型；密钥和地址默认继承主生成服务
DEFAULT_NER_MODEL = "gemini-3.1-flash-lite"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_DETECT_MODEL = "gemini-3.1-flash-lite"

DEFAULT_CONCURRENT = 20
DEFAULT_MAX_TOKENS = 65536
DEFAULT_EMBED_WORKERS = 16

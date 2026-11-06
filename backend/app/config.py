"""\n配置管理\n统一从项目根目录的 .env 文件加载配置\n"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    # 默认指向本地 Ollama（经本地 /v1 转发端点，见 app/api/llm_proxy.py）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:5001/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'lfm3:latest')

    # ----- 本地 LLM 控制（全部流量经 /v1 转发到下方上游 Ollama）-----
    # 转发目标：真正提供模型的 Ollama 服务（localhost 优先，保护设备内存，单实例推理）
    LLM_UPSTREAM_BASE_URL = os.environ.get('LLM_UPSTREAM_BASE_URL', 'http://localhost:11434')
    # 思考模式：'none'=关闭（快、稳定）| 'high'=开启（更慢、质量更高）| 空=模型默认(默认开启更强推理)
    # 可在“AI 监控”界面实时切换，无需重启
    LLM_REASONING_EFFORT = os.environ.get('LLM_REASONING_EFFORT', 'none')
    # 并发推理上限（同一模型实例内的并行请求数）与上下文窗口
    LLM_NUM_PARALLEL = int(os.environ.get('LLM_NUM_PARALLEL', '4'))
    LLM_CONTEXT_LENGTH = int(os.environ.get('LLM_CONTEXT_LENGTH', '8192'))
    # 单次本地 LLM 调用的超时（秒）。长 prefill 冷启动需要余量；须小于前端上限(1800s)
    LLM_PROXY_TIMEOUT = int(os.environ.get('LLM_PROXY_TIMEOUT', '1800'))
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')

    # 图后端选择: 'zep'（默认，Zep Cloud）| 'graphiti'（本地 Graphiti + Kuzu + Ollama，
    # 完全本地运行、无需任何 API Key、无云端摄取超时）。切换后需重启后端。
    GRAPH_BACKEND = os.environ.get('GRAPH_BACKEND', 'zep').strip().lower()
    # 本地图谱数据根目录（每个 graph_id 一个子目录：Kuzu 库 + 本体 + 批次记录）
    GRAPHITI_DATA_DIR = os.environ.get(
        'GRAPHITI_DATA_DIR',
        os.path.join(os.path.dirname(__file__), '../data/graphiti')
    )
    # 本地图谱使用的 LLM（默认复用 MiroFish 的本地模型；经 /v1 代理转发，思考默认关闭）
    GRAPHITI_LLM_MODEL = os.environ.get('GRAPHITI_LLM_MODEL', LLM_MODEL_NAME)
    GRAPHITI_LLM_MAX_TOKENS = int(os.environ.get('GRAPHITI_LLM_MAX_TOKENS', '2048'))
    # 结构化输出模式：json_schema=由 Ollama 约束解码保证 JSON 结构（推荐，本地实测可靠）
    # json_object=仅提示词约束（对小模型不稳）。
    GRAPHITI_STRUCTURED_OUTPUT_MODE = os.environ.get('GRAPHITI_STRUCTURED_OUTPUT_MODE', 'json_schema')
    # 本地 Embedding（Ollama nomic-embed-text:v1.5 → 768 维；更换模型需同步维度）
    GRAPHITI_EMBEDDING_MODEL = os.environ.get('GRAPHITI_EMBEDDING_MODEL', 'nomic-embed-text:v1.5')
    GRAPHITI_EMBEDDING_DIM = int(os.environ.get('GRAPHITI_EMBEDDING_DIM', '768'))
    # Graphiti 内部并发（本地单模型，Ollama 天然串行；2 足够）
    GRAPHITI_MAX_COROUTINES = int(os.environ.get('GRAPHITI_MAX_COROUTINES', '2'))

    @classmethod
    def is_local_graph_backend(cls) -> bool:
        """当前是否使用本地 Graphiti 后端（无 Zep 密钥、无云端超时）。"""
        return cls.GRAPH_BACKEND in ('graphiti', 'local')

    @classmethod
    def zep_api_key_required(cls) -> bool:
        """只有 Zep Cloud 后端才强制 ZEP_API_KEY。"""
        return not cls.is_local_graph_backend()
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    # ===== Model runtime (Phase 1: MLX/GGUF/Ollama behind the /v1 proxy, HARD UNLOAD) =====
    # 'auto' picks mlx on macOS, gguf elsewhere; user can force one in .env / Model Manager.
    MODEL_RUNTIME_BACKEND = os.environ.get('MODEL_RUNTIME_BACKEND', 'auto').strip().lower()
    # RAM budget (GB) the model manager keeps free on top of the OS (16 GB Mac → 11 GB default).
    MODEL_RAM_BUDGET_GB = float(os.environ.get('MODEL_RAM_BUDGET_GB', '11'))
    # Idle seconds before a loaded-but-unreferenced model is hard-unloaded (safety net).
    MODEL_IDLE_UNLOAD_SECONDS = int(os.environ.get('MODEL_IDLE_UNLOAD_SECONDS', '120'))
    # Seconds to wait for an idle-unloaded model's footprint to actually drop before WARN.
    MODEL_UNLOAD_RSS_FLOOR_MB = int(os.environ.get('MODEL_UNLOAD_RSS_FLOOR_MB', '150'))
    # llama.cpp server binary (llama-cpp-python bundles one; standalone install also works).
    LLAMA_SERVER_BIN = os.environ.get('LLAMA_SERVER_BIN', 'llama-server')
    # GGUF server context + threads (0 = model default). Kept modest for a 16 GB Mac.
    MODEL_GGUF_CTX = int(os.environ.get('MODEL_GGUF_CTX', '8192'))
    MODEL_GGUF_THREADS = int(os.environ.get('MODEL_GGUF_THREADS', '0'))

    # Project root = Omnidroids/ (three levels up from this file: app/ -> backend/ -> root/).
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    # Downloaded/local model weights live ONLY inside this folder (never ~/.ollama, never global).
    MODELS_DIR = os.environ.get('MODELS_DIR', os.path.join(PROJECT_ROOT, 'models'))
    # Installed-model registry + role assignments (git-ignored contents except weights policy).
    MODELS_REGISTRY_PATH = os.environ.get(
        'MODELS_REGISTRY_PATH', os.path.join(MODELS_DIR, 'registry.json')
    )

    @classmethod
    def default_model_backend(cls) -> str:
        """Resolve MODEL_RUNTIME_BACKEND='auto' to a concrete backend for this OS."""
        forced = (cls.MODEL_RUNTIME_BACKEND or 'auto').strip().lower()
        if forced in {'mlx', 'gguf', 'ollama'}:
            return forced
        try:
            import platform as _p
            if _p.system() == 'Darwin' and _p.machine() == 'arm64':
                return 'mlx'
        except Exception:
            pass
        return 'gguf'

    
    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置"""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        if cls.zep_api_key_required() and not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未配置")
        if os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL 不受支持；MiroFish 仅连接 Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors

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
    # 默认指向本地 /v1 转发端点（见 app/api/llm_proxy.py）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:5001/v1')
    # Ollama (B5/#14) tagged its models ("lfm3:latest"); this repo runs MLX/GGUF
    # weights identified by directory name, so the default is the 'small' role's
    # model id. The value is a NAME only: the /v1 proxy maps it to whatever model
    # the Model Manager has assigned to that role (see app/models/assignment.py),
    # and an unknown name falls through to that same role.
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'LFM3-1.7B-MLX-4bit')

    # ---- Model for steps 4-5 (Report Generation / Deep Interaction) -----
    # Owner decision (issue #22): steps 1-3 (Graph Build, Env Setup, Run
    # Simulation) keep using LLM_MODEL_NAME above (the /v1 proxy maps that name to
    # the 'small' role); steps 4-5 -- both served by ReportAgent -- use the 'big'
    # role instead (see app/models/assignment.py).
    # Only the ROLE is stored here, never a model name: it is re-read from the
    # Model Manager's models/registry.json on every call, so the owner reassigns
    # the role (or points the override below at another 9B-class model) with no
    # code change. An unassigned role fails loudly -- never a silent fallback.
    REPORT_LLM_ROLE = (os.environ.get('REPORT_LLM_ROLE', 'big') or 'big').strip().lower()
    # Optional explicit model name for those steps (must be installed in the Model
    # Manager). Empty = use the role assignment above; when set, it wins.
    REPORT_LLM_MODEL_NAME = (os.environ.get('REPORT_LLM_MODEL_NAME', '') or '').strip()

    # ----- 本地 LLM 控制（全部流量经 /v1 转发到本地模型运行时）-----
    # 思考模式：'none'=关闭（快、稳定）| 'high'=开启（更慢、质量更高）| 空=模型默认(默认开启更强推理)
    # 可在“AI 监控”界面实时切换，无需重启
    LLM_REASONING_EFFORT = os.environ.get('LLM_REASONING_EFFORT', 'none')
    # 并发推理上限（同一模型实例内的并行请求数）与上下文窗口
    LLM_NUM_PARALLEL = int(os.environ.get('LLM_NUM_PARALLEL', '4'))
    LLM_CONTEXT_LENGTH = int(os.environ.get('LLM_CONTEXT_LENGTH', '8192'))
    # Explicit output budget for the ontology call (step-1 prepare; #51).
    # The call used to pass max_tokens=None ("let the provider use its
    # model-specific limit"), MLXRunner.generate drops a falsy cap, and mlx-vlm
    # then applies its own DEFAULT_MAX_TOKENS=2048 (mlx_vlm/generate/common.py):
    # the 1.7B's ontology JSON truncated (finish_reason=length -> HTTP 502) and
    # the byte-identical retry truncated again. The budget therefore travels
    # with the request and the runner logs the value it resolved, so it is never
    # inherited silently. Keep it below LLM_CONTEXT_LENGTH minus the prompt.
    ONTOLOGY_LLM_MAX_TOKENS = int(os.environ.get('ONTOLOGY_LLM_MAX_TOKENS', '4096'))
    # 单次本地 LLM 调用的超时（秒）。长 prefill 冷启动需要余量；须小于前端上限(1800s)
    LLM_PROXY_TIMEOUT = int(os.environ.get('LLM_PROXY_TIMEOUT', '1800'))
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')

    # 图后端选择: 'zep'（默认，Zep Cloud）| 'graphiti'（本地 Graphiti + Kuzu + 本地模型，
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
    # 结构化输出模式：json_schema/json_object 都由 /v1 代理转换为本地运行时的
    # 提示词级严格 JSON + 修复解析（对小模型实测可靠）。
    GRAPHITI_STRUCTURED_OUTPUT_MODE = os.environ.get('GRAPHITI_STRUCTURED_OUTPUT_MODE', 'json_schema')
    # 本地 Embedding（sentence-transformers 本地模型，B5 起进程内运行；更换模型需同步维度）。
    # 默认 Snowflake/snowflake-arctic-embed-s（384-d，33M）：本地烘焙中检索准确率
    # 6/6、吞吐 ~400 docs/s、单条延迟 ~13ms，均优于 MiniLM-L6-v2（5/6、74/s、38ms）与
    # granite-97m 多语言版（5/6、56/s、97M 参数）——见 scratch/embedder_bakeoff.py。
    GRAPHITI_EMBEDDING_MODEL = os.environ.get(
        'GRAPHITI_EMBEDDING_MODEL', 'Snowflake/snowflake-arctic-embed-s')
    GRAPHITI_EMBEDDING_DIM = int(os.environ.get('GRAPHITI_EMBEDDING_DIM', '384'))
    # arctic 的非对称检索提示（查询/文档两侧都加；对称使用时同样 6/6，无副作用）
    GRAPHITI_EMBEDDING_QUERY_PROMPT = os.environ.get(
        'GRAPHITI_EMBEDDING_QUERY_PROMPT',
        'Represent this sentence for searching relevant passages: ')
    # Graphiti 内部并发（本地单模型顺序服务；2 足够）
    GRAPHITI_MAX_COROUTINES = int(os.environ.get('GRAPHITI_MAX_COROUTINES', '2'))

    @classmethod
    def is_local_graph_backend(cls) -> bool:
        """当前是否使用本地 Graphiti 后端（无 Zep 密钥、无云端超时）。"""
        return cls.GRAPH_BACKEND in ('graphiti', 'local')

    @classmethod
    def zep_api_key_required(cls) -> bool:
        """只有 Zep Cloud 后端才强制 ZEP_API_KEY。"""
        return not cls.is_local_graph_backend()
    
    # 前端构建产物目录（§10：后端托管 SPA，单进程 :5001，无 Node/Vite 运行时）。
    # 缺失时后端仅提供 API（开发期未构建时的降级行为）。
    FRONTEND_DIST_DIR = os.environ.get(
        'FRONTEND_DIST_DIR',
        os.path.abspath(os.path.join(os.path.dirname(__file__), '../../frontend/dist'))
    )
    
    # §8：会话主页面持久化（sqlite3 stdlib，WAL）。仓库根 data/ 已 git-ignored。
    SESSIONS_DB_PATH = os.environ.get(
        'SESSIONS_DB_PATH',
        os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/omnidroids.db'))
    )
    
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

    # ===== Model runtime (MLX/GGUF behind the /v1 proxy, HARD UNLOAD) =====
    # 'auto' picks mlx on macOS, gguf elsewhere; user can force one in .env / Model Manager.
    MODEL_RUNTIME_BACKEND = os.environ.get('MODEL_RUNTIME_BACKEND', 'auto').strip().lower()
    # RAM budget (GB) the model manager keeps free on top of the OS (16 GB Mac → 11 GB default).
    MODEL_RAM_BUDGET_GB = float(os.environ.get('MODEL_RAM_BUDGET_GB', '11'))
    # Max co-resident loaded models (Model Manager 'concurrency' setting default).
    MODEL_MAX_CO_RESIDENT = int(os.environ.get('MODEL_MAX_CO_RESIDENT', '4'))
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
    # Downloaded/local model weights live ONLY inside this folder (never global paths).
    MODELS_DIR = os.environ.get('MODELS_DIR', os.path.join(PROJECT_ROOT, 'models'))
    # Installed-model registry + role assignments (git-ignored contents except weights policy).
    MODELS_REGISTRY_PATH = os.environ.get(
        'MODELS_REGISTRY_PATH', os.path.join(MODELS_DIR, 'registry.json')
    )
    # Model Manager settings (concurrency / RAM budget / idle seconds) — machine-local,
    # git-ignored like the registry; the manager re-reads it at acquire/idle time.
    MODEL_SETTINGS_PATH = os.environ.get(
        'MODEL_SETTINGS_PATH', os.path.join(MODELS_DIR, 'settings.json')
    )
    # Confinement: every HF artifact (downloads, hub cache) lives inside the
    # Omnidroids folder — never ~/.cache (HANDOFF §5.1; the launcher exports it too).
    HF_CACHE_DIR = os.environ.get('HF_CACHE_DIR', os.path.join(PROJECT_ROOT, '.cache', 'hf'))

    @classmethod
    def default_model_backend(cls) -> str:
        """Resolve MODEL_RUNTIME_BACKEND='auto' to a concrete backend for this OS."""
        forced = (cls.MODEL_RUNTIME_BACKEND or 'auto').strip().lower()
        if forced in {'mlx', 'gguf'}:
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

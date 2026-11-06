"""
MiroFish Backend - Flask应用工厂
"""

import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish Backend 启动中...")
        logger.info("=" * 50)
    
    # 启用CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")

    # 本地图后端（Graphiti + Kuzu）：安装 atexit / 信号处理钩子，保证重启时
    # Kuzu 库被干净关闭（否则下次进程首次写入可能触发原生 SIGBUS）
    if Config.is_local_graph_backend():
        from .utils.graphiti_zep import register_runtime_shutdown
        register_runtime_shutdown()
        if should_log_startup:
            logger.info("本地 Graphiti 图后端已启用 (Kuzu + 本地 LLM, 无 API Key)")

    # 模型运行时：启动 ModelManager（事件循环 + 空闲硬卸载看门狗），并注册退出清理。
    try:
        from .models.manager import get_model_manager
        _model_manager = get_model_manager()
        _model_manager.start()
        import atexit as _atexit
        _atexit.register(_model_manager.shutdown)
        if should_log_startup:
            logger.info(
                "模型运行时已启动 (backend=%s, RAM预算=%sGB, 空闲卸载=%ss)",
                Config.default_model_backend(), Config.MODEL_RAM_BUDGET_GB,
                Config.MODEL_IDLE_UNLOAD_SECONDS,
            )
    except Exception as exc:  # pragma: no cover - never block app start
        logger.error("模型运行时启动失败: %s", exc)
    
    # 请求日志中间件
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"请求: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"请求体: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"响应: {response.status_code}")
        return response
    
    # 注册蓝图
    from .api import (
        graph_bp, simulation_bp, report_bp, llm_proxy_bp, llm_monitor_bp, models_bp,
    )
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    # 本地 LLM：OpenAI 兼容运行时端点(/v1) + 监控(/api/llm) + 模型管理(/api/models)
    app.register_blueprint(llm_proxy_bp, url_prefix='/v1')
    app.register_blueprint(llm_monitor_bp, url_prefix='/api/llm')
    app.register_blueprint(models_bp, url_prefix='/api/models')
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}
    
    # §10：托管前端构建产物（frontend/dist）——最后注册，保证 API 路由的优先级。
    _register_spa_serving(app)
    
    if should_log_startup:
        logger.info("MiroFish Backend 启动完成")
    
    return app


def _register_spa_serving(app):
    """HANDOFF §10：由后端单进程托管构建后的 Vue SPA（替代 Node/Vite 开发服务器）。

    - 真实文件（/assets/... 等）直接从 frontend/dist 返回；
    - 其余 GET 请求回退到 index.html（vue-router history 模式）；
    - /api、/v1、/health 前缀绝不落入 SPA 壳（返回 API 404）。
    dist 目录不存在时静默跳过（开发期未构建的降级）。
    """

    dist_dir = Config.FRONTEND_DIST_DIR
    if not dist_dir or not os.path.isdir(dist_dir):
        return
    index_path = os.path.join(dist_dir, 'index.html')
    if not os.path.isfile(index_path):
        return

    api_prefixes = ('api/', 'v1/')  # /health 由上面的静态路由优先匹配

    @app.route('/', methods=['GET'])
    def spa_index():
        return send_from_directory(dist_dir, 'index.html')

    @app.route('/<path:path>', methods=['GET'])
    def spa_fallback(path):
        if path.startswith(api_prefixes):
            return jsonify({'code': 404, 'message': 'Not Found'}), 404
        target = os.path.join(dist_dir, path)
        if os.path.isfile(target):
            return send_from_directory(dist_dir, path)
        # history 模式：未知前端路由一律回退到 index.html
        return send_from_directory(dist_dir, 'index.html')


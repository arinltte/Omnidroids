"""
API路由模块
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)
llm_proxy_bp = Blueprint('llm_proxy', __name__)
llm_monitor_bp = Blueprint('llm_monitor', __name__)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401
from . import llm_proxy  # noqa: E402, F401  (OpenAI-compatible local forwarding)
from . import llm_monitor  # noqa: E402, F401


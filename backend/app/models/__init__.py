"""
数据模型模块
"""

from .task import TaskManager, TaskStatus
from .project import Project, ProjectStatus, ProjectManager
from .store import ModelStore, ROLES
from .runtime import detect_backend, make_runner, MLXRunner, GGUFRunner, OllamaRunner, GenResult
from .manager import ModelManager, ModelError, get_model_manager

__all__ = [
    'TaskManager', 'TaskStatus', 'Project', 'ProjectStatus', 'ProjectManager',
    'ModelStore', 'ROLES', 'detect_backend', 'make_runner',
    'MLXRunner', 'GGUFRunner', 'OllamaRunner', 'GenResult',
    'ModelManager', 'ModelError', 'get_model_manager',
]


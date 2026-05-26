"""Pipeline orchestration entry points."""

from .config import Stage1Config, Stage3Config
from .stage1_extractor import Stage1Extractor
from .stage2_structurer import Stage2Config, Stage2Structurer
from .stage3_enhancer import Stage3Enhancer

# New Knowledge pipeline
from .knowledge_config import (
    KnowledgeConfig,
    DocumentFolder,
    DocumentFolderManager,
    FOLDER_INPUT,
    FOLDER_INITIAL_MARKDOWN,
    FOLDER_IMAGES,
    FOLDER_FINAL_MARKDOWN,
    FOLDER_JSON,
)
from .knowledge_converter import KnowledgeConverter
from .knowledge_enhancer import KnowledgeEnhancer

# Knowledge update pipeline
from .proposal_generator import (
    ProposalGenerator,
    Proposal,
    ProposedAction,
    ActionType,
    UpdateMode,
    ChangeDetail,
)
from .knowledge_updater import KnowledgeUpdater
from .link_validator import LinkValidator, LinkIssue

__all__ = [
    # Legacy pipeline
    "Stage1Config",
    "Stage1Extractor",
    "Stage2Config",
    "Stage2Structurer",
    "Stage3Config",
    "Stage3Enhancer",
    # New Knowledge pipeline
    "KnowledgeConfig",
    "DocumentFolder",
    "DocumentFolderManager",
    "KnowledgeConverter",
    "KnowledgeEnhancer",
    "FOLDER_INPUT",
    "FOLDER_INITIAL_MARKDOWN",
    "FOLDER_IMAGES",
    "FOLDER_FINAL_MARKDOWN",
    "FOLDER_JSON",
    # Knowledge update pipeline
    "ProposalGenerator",
    "Proposal",
    "ProposedAction",
    "ActionType",
    "UpdateMode",
    "ChangeDetail",
    "KnowledgeUpdater",
    "LinkValidator",
    "LinkIssue",
]
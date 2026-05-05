"""Agent Bricks Service - Manage Genie Spaces, Knowledge Assistants, and Multi-Agent Supervisors.

Includes TypedDict definitions for API responses based on api_ka.proto and api_mas.proto.
Uses the Databricks Python SDK for Knowledge Assistants and Genie operations where possible.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TypedDict

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.knowledgeassistants import (
  FileTableSpec,
  FilesSpec,
  IndexSpec,
  KnowledgeAssistant,
  KnowledgeSource,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Type Definitions (from api_ka.proto and api_mas.proto)
# ============================================================================

class TileDict(TypedDict, total=False):
    """Tile metadata common to KA and MAS."""
    tile_id: str
    name: str
    description: Optional[str]
    instructions: Optional[str]
    tile_type: str
    created_timestamp_ms: int
    last_updated_timestamp_ms: int
    user_id: str


class KnowledgeSourceDict(TypedDict, total=False):
    """Knowledge source configuration for KA."""
    knowledge_source_id: str
    files_source: Dict[str, Any]  # Contains: name, type, files: {path: ...}


class KnowledgeAssistantStatusDict(TypedDict):
    """KA endpoint status."""
    endpoint_status: str  # ONLINE, OFFLINE, PROVISIONING, NOT_READY


class KnowledgeAssistantDict(TypedDict, total=False):
    """Complete Knowledge Assistant response."""
    tile: TileDict
    knowledge_sources: List[KnowledgeSourceDict]
    status: KnowledgeAssistantStatusDict


class KnowledgeAssistantResponseDict(TypedDict):
    """GET /knowledge-assistants/{tile_id} response."""
    knowledge_assistant: KnowledgeAssistantDict


class KnowledgeAssistantExampleDict(TypedDict, total=False):
    """KA example question."""
    example_id: str
    question: str
    guidelines: List[str]
    feedback_records: List[Dict[str, Any]]


class KnowledgeAssistantListExamplesResponseDict(TypedDict, total=False):
    """List examples response."""
    examples: List[KnowledgeAssistantExampleDict]
    tile_id: str
    next_page_token: Optional[str]


class BaseAgentDict(TypedDict, total=False):
    """Agent configuration for MAS."""
    name: str
    description: str
    agent_type: str  # genie, ka, app, etc.
    genie_space: Optional[Dict[str, str]]  # {id: ...}
    serving_endpoint: Optional[Dict[str, str]]  # {name: ...}
    app: Optional[Dict[str, str]]  # {name: ...}
    unity_catalog_function: Optional[Dict[str, Any]]


class MultiAgentSupervisorStatusDict(TypedDict):
    """MAS endpoint status."""
    endpoint_status: str  # ONLINE, OFFLINE, PROVISIONING, NOT_READY


class MultiAgentSupervisorDict(TypedDict, total=False):
    """Complete Multi-Agent Supervisor response."""
    tile: TileDict
    agents: List[BaseAgentDict]
    status: MultiAgentSupervisorStatusDict


class MultiAgentSupervisorResponseDict(TypedDict):
    """GET /multi-agent-supervisors/{tile_id} response."""
    multi_agent_supervisor: MultiAgentSupervisorDict


class MultiAgentSupervisorExampleDict(TypedDict, total=False):
    """MAS example question."""
    example_id: str
    question: str
    guidelines: List[str]
    feedback_records: List[Dict[str, Any]]


class MultiAgentSupervisorListExamplesResponseDict(TypedDict, total=False):
    """List examples response."""
    examples: List[MultiAgentSupervisorExampleDict]
    tile_id: str
    next_page_token: Optional[str]


class GenieSpaceDict(TypedDict, total=False):
    """Genie Space (Data Room) response.

    Note: Genie uses /api/2.0/data-rooms endpoint (not defined in KA/MAS protos).
    """
    space_id: str
    id: str  # Same as space_id
    display_name: str
    description: Optional[str]
    warehouse_id: str
    table_identifiers: List[str]
    run_as_type: str  # VIEWER, OWNER, etc.
    created_timestamp: int
    last_updated_timestamp: int
    user_id: str
    folder_node_internal_name: Optional[str]
    sample_questions: Optional[List[str]]


class CuratedQuestionDict(TypedDict, total=False):
    """Curated question for Genie space."""
    question_id: str
    data_space_id: str
    question_text: str
    question_type: str  # SAMPLE_QUESTION, BENCHMARK
    answer_text: Optional[str]
    is_deprecated: bool


class GenieListQuestionsResponseDict(TypedDict, total=False):
    """List curated questions response."""
    curated_questions: List[CuratedQuestionDict]


class InstructionDict(TypedDict, total=False):
    """Genie instruction (text, SQL, or certified answer)."""
    instruction_id: str
    title: str
    content: str
    instruction_type: str  # TEXT_INSTRUCTION, SQL_INSTRUCTION, CERTIFIED_ANSWER


class GenieListInstructionsResponseDict(TypedDict, total=False):
    """List instructions response."""
    instructions: List[InstructionDict]


class EvaluationRunDict(TypedDict, total=False):
    """Evaluation run metadata."""
    mlflow_run_id: str
    tile_id: str
    name: Optional[str]
    created_timestamp_ms: int
    last_updated_timestamp_ms: int


class ListEvaluationRunsResponseDict(TypedDict, total=False):
    """List evaluation runs response."""
    evaluation_runs: List[EvaluationRunDict]
    next_page_token: Optional[str]


# ============================================================================
# Enums and Data Classes
# ============================================================================


class TileType(Enum):
  """Tile types from the protobuf definition."""

  UNSPECIFIED = 0
  KIE = 1  # Knowledge Indexing Engine
  T2T = 2  # Text to Text
  KA = 3  # Knowledge Assistant
  MAO = 4  # Deprecated
  MAS = 5  # Model Serving?


class EndpointStatus(Enum):
  """Vector Search Endpoint status values."""

  ONLINE = 'ONLINE'
  OFFLINE = 'OFFLINE'
  PROVISIONING = 'PROVISIONING'
  NOT_READY = 'NOT_READY'


class Permission(Enum):
  """Standard Databricks permissions for sharing resources."""

  CAN_READ = 'CAN_READ'  # View/read access
  CAN_WRITE = 'CAN_WRITE'  # Write/edit access
  CAN_RUN = 'CAN_RUN'  # Execute/run access
  CAN_MANAGE = 'CAN_MANAGE'  # Full management access including ACLs
  CAN_VIEW = 'CAN_VIEW'  # View metadata only


@dataclass(frozen=True)
class KAIds:
  tile_id: str
  name: str


@dataclass(frozen=True)
class GenieIds:
  space_id: str
  display_name: str


@dataclass(frozen=True)
class MASIds:
  tile_id: str
  name: str


class AgentBricksManager:
  """Unified wrapper for Agent Bricks tiles (Knowledge Assistants, Multi-Agent Supervisors, and Genie spaces).

  Works with /2.0/knowledge-assistants*, /2.0/multi-agent-supervisors*, /2.0/data-rooms*, and /2.0/tiles* endpoints.

  Key operations:
    Common tile ops (work for both KA and MAS):
    - get(tile_id): Get any tile by ID
    - delete(tile_id): Delete any tile
    - find_by_name(name, tile_type): Find tile by name
    - share(tile_id, changes): Share tile with users/groups

    KA-specific ops (prefixed with ka_):
    - ka_create_or_update(): Create or update a KA with knowledge sources
    - ka_create(): Create KA with specified knowledge sources
    - ka_update_sources(): Add/remove knowledge sources
    - ka_sync_sources(): Trigger re-index of all sources
    - ka_get_endpoint_status(): Get ka endpoint status
    - ka_add_examples_batch(): Add example questions

    MAS-specific ops (prefixed with mas_):
    - mas_create(): Create a Multi-Agent Supervisor with agents
    - mas_update(): Update MAS configuration and agents
    - mas_get_endpoint_status(): Get mas endpoint status
    - mas_create_example(): Add example question
    - mas_list_examples(): List all examples
    - mas_update_example(): Update an example
    - mas_delete_example(): Delete an example
    - mas_add_examples_batch(): Add multiple examples in parallel

    Genie-specific ops (prefixed with genie_):
    - genie_get(): Get Genie space by ID
    - genie_create(): Create a Genie space with table identifiers
    - genie_update(): Update Genie space configuration (metadata + sample questions)
    - genie_update_sample_questions(): Update only sample questions (replaces all)
    - genie_delete(): Delete Genie space
    - genie_list_questions(): List curated questions
    - genie_add_sample_question(): Add a single sample question
    - genie_add_sample_questions_batch(): Add multiple sample questions
    - genie_add_text_instruction(): Add text instruction/notes
    - genie_add_sql_instruction(): Add SQL query example
    - genie_add_sql_instructions_batch(): Add multiple SQL example queries
    - genie_add_sql_function(): Add SQL function (certified answer)
    - genie_add_sql_functions_batch(): Add multiple SQL functions
    - genie_add_benchmark(): Add benchmark question with expected answer
    - genie_add_benchmarks_batch(): Add multiple benchmarks

  Notes:
    * Both KA and MAS are Tile types; deletion uses /2.0/tiles/{tile_id}
    * KA FilesSource JSON shape (per proto):
        {
          "files_source": {
            "name": "...",
            "type": "files",
            "description": "...",
            "files": {"path": "<UC VOLUME PATH>"}
          }
        }
    * MAS BaseAgent JSON shape (per proto):
        {
          "name": "...",
          "description": "...",
          "agent_type": "...",
          "genie_space": {"id": "..."}  // or serving_endpoint, app, etc.
        }
  """

  def __init__(
    self, w: WorkspaceClient, *, default_timeout_s: int = 600, default_poll_s: float = 2.0
  ):
    self.w: WorkspaceClient = w
    self.default_timeout_s = default_timeout_s
    self.default_poll_s = default_poll_s

  @staticmethod
  def sanitize_name(name: str) -> str:
    """Sanitize a name to ensure it's alphanumeric with only hyphens and underscores.

    Args:
        name: The original name

    Returns:
        Sanitized name that complies with Databricks naming requirements
    """
    # Replace spaces with underscores
    sanitized = name.replace(' ', '_')

    # Replace any character that is not alphanumeric, hyphen, or underscore with underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', sanitized)

    # Remove consecutive underscores or hyphens
    sanitized = re.sub(r'[_-]{2,}', '_', sanitized)

    # Remove leading/trailing underscores or hyphens
    sanitized = sanitized.strip('_-')

    # If the name is empty after sanitization, use a default
    if not sanitized:
      sanitized = 'knowledge_assistant'

    logger.debug(f"Sanitized name: '{name}' -> '{sanitized}'")
    return sanitized

  # ---------- KA-specific operations ----------

  def ka_create_or_update(
    self,
    display_name: str,
    description: str,
    knowledge_sources: Optional[List[Dict[str, Any]]] = None,
    instructions: Optional[str] = None,
    ka_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Create or update a Knowledge Assistant with knowledge sources.

    This method handles the complete workflow:
    1. Creates a new KA or updates an existing one
    2. Manages knowledge sources (add new, remove old)

    Args:
        display_name: Display name for the Knowledge Assistant
        description: Description of the KA (required for creation)
        knowledge_sources: Optional list of knowledge source configs. Each should have:
            - display_name: Name for the source
            - description: Description of the source
            - source_type: "files", "index", or "file_table"
            - files_path: Path to files (if source_type is "files")
            - Or other source-specific fields
        instructions: Optional instructions for the KA
        ka_id: Optional KA ID - if provided, update existing KA; otherwise check by name

    Returns:
        KA data dictionary with 'operation' field ('created' or 'updated')
    """
    sanitized_name = self.sanitize_name(display_name)
    if sanitized_name != display_name:
      logger.info(f"Display name sanitized from '{display_name}' to '{sanitized_name}'")

    existing_ka = None
    operation = 'created'

    # First try by ID if provided
    if ka_id:
      logger.info(f'Checking if KA with id={ka_id} exists...')
      existing_ka = self.ka_get(ka_id)
      if existing_ka:
        operation = 'updated'
        logger.info(f'Found existing KA with id={ka_id}')
      else:
        logger.info(f'No existing KA found with id={ka_id}, will create new one')

    if existing_ka:
      # Wait for KA to be ready before updating
      logger.info(f'Checking if KA {ka_id} is ready for update...')
      if not self.ka_is_ready_for_update(ka_id):
        current_state = self.ka_get_endpoint_status(ka_id)
        logger.info(f'KA {ka_id} is not ready for update (state: {current_state})')
        raise Exception(
          f"Knowledge Assistant {ka_id} is not ready for update (state: {current_state}). "
          "You can wait or ask the assistant to delete and create a new one if it's stuck."
        )

      logger.info(f'KA {ka_id} is ready for update, proceeding...')
      # Update existing KA metadata
      result = self.ka_update(
        ka_id,
        display_name=sanitized_name,
        description=description,
        instructions=instructions,
      )

      # Handle knowledge sources - replace all existing with new ones
      if knowledge_sources is not None:
        logger.info(f'Updating knowledge sources for KA {ka_id}...')
        # Get current sources
        current_sources = self.ka_list_sources(ka_id)
        logger.info(f'Current KA has {len(current_sources)} sources')

        # Delete existing sources
        for source in current_sources:
          source_id = source.get('id')
          if source_id:
            logger.info(f'Deleting existing source {source_id}')
            self.ka_delete_source(ka_id, source_id)

        # Add new sources
        for source_config in knowledge_sources:
          self._add_knowledge_source_from_config(ka_id, source_config)

      logger.info(f'KA {ka_id} updated successfully')
      result = self.ka_get(ka_id)
    else:
      # Create new KA
      logger.info(f'Creating new KA with display_name={sanitized_name}')
      result = self.ka_create(
        display_name=sanitized_name,
        description=description,
        instructions=instructions,
      )
      ka_id = result['id']
      operation = 'created'
      logger.info(f'KA created successfully with id={ka_id}')

      # Add knowledge sources
      if knowledge_sources:
        logger.info(f'Adding {len(knowledge_sources)} knowledge sources to KA {ka_id}...')
        for source_config in knowledge_sources:
          self._add_knowledge_source_from_config(ka_id, source_config)

      # Refresh KA data
      result = self.ka_get(ka_id)

    # Add operation status to result
    if result:
      result['operation'] = operation

    return result

  def _add_knowledge_source_from_config(self, ka_id: str, source_config: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to add a knowledge source from a config dictionary.

    Args:
        ka_id: The KA ID
        source_config: Config dict with keys: display_name, description, source_type, and source-specific fields

    Returns:
        Created source data
    """
    return self.ka_create_source(
      ka_id=ka_id,
      display_name=source_config.get('display_name', 'Source'),
      description=source_config.get('description', ''),
      source_type=source_config.get('source_type', 'files'),
      files_path=source_config.get('files_path'),
      index_name=source_config.get('index_name'),
      index_text_col=source_config.get('index_text_col'),
      index_doc_uri_col=source_config.get('index_doc_uri_col'),
      file_table_name=source_config.get('file_table_name'),
      file_table_file_col=source_config.get('file_table_file_col'),
    )

  def ka_create(
    self,
    display_name: str,
    description: str,
    instructions: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Create a Knowledge Assistant using the Python SDK.

    Note: Knowledge sources must be added separately after creation using ka_create_source().

    Args:
        display_name: Display name for the Knowledge Assistant (4-63 chars, alphanumeric with . - _)
        description: Description of what this agent can do (required)
        instructions: Optional additional global instructions for answer generation

    Returns:
        Created KA data with structure:
        {
          "id": "...",
          "name": "knowledge-assistants/{id}",
          "display_name": "...",
          "description": "...",
          "instructions": "...",
          "endpoint_name": "...",
          "state": "CREATING",
          "create_time": "...",
          "creator": "...",
          "experiment_id": "..."
        }
    """
    sanitized_name = self.sanitize_name(display_name)
    if sanitized_name != display_name:
      logger.info(f"Display name sanitized from '{display_name}' to '{sanitized_name}'")

    ka = KnowledgeAssistant(display_name=sanitized_name, description=description, instructions=instructions)
    logger.debug(f'Creating KA with display_name={sanitized_name}')
    result = self.w.knowledge_assistants.create_knowledge_assistant(knowledge_assistant=ka)
    return result.as_dict()

  # Note: create_from_volume has been removed. Use create_knowledge_assistant instead.

  def ka_delete(self, ka_id: str) -> None:
    """Delete a Knowledge Assistant using the Python SDK.

    Args:
        ka_id: The KA ID (UUID)
    """
    name = f'knowledge-assistants/{ka_id}'
    self.w.knowledge_assistants.delete_knowledge_assistant(name)

  def delete(self, tile_id: str) -> None:
    """Delete the KA tile (deprecated, use ka_delete instead)."""
    self.ka_delete(tile_id)

  def ka_get(self, ka_id: str) -> Optional[Dict[str, Any]]:
    """Get Knowledge Assistant by ID using the Python SDK.

    Args:
        ka_id: The Knowledge Assistant ID (UUID)

    Returns:
        KA data dictionary with structure:
        {
          "id": "...",
          "name": "knowledge-assistants/{id}",
          "display_name": "...",
          "description": "...",
          "instructions": "...",
          "endpoint_name": "...",
          "state": "CREATING|ACTIVE|FAILED",
          "create_time": "...",
          "creator": "...",
          "experiment_id": "...",
          "error_info": "..."  # Only when state is FAILED
        }
        Returns None if not found or doesn't exist.
    """
    try:
      name = f'knowledge-assistants/{ka_id}'
      result = self.w.knowledge_assistants.get_knowledge_assistant(name)
      return result.as_dict()
    except Exception as e:
      error_str = str(e).lower()
      if 'does not exist' in error_str or 'not found' in error_str:
        return None
      raise

  def ka_get_endpoint_status(self, ka_id: str) -> Optional[str]:
    """Get the state of a Knowledge Assistant.

    Uses the official API which returns state as CREATING, ACTIVE, or FAILED.

    Args:
        ka_id: The KA ID (UUID)

    Returns:
        The state string (CREATING, ACTIVE, FAILED) or None if not found.
        Note: ACTIVE corresponds to the old ONLINE status.
    """
    ka = self.ka_get(ka_id)
    if not ka:
      return None

    return ka.get('state')

  def ka_is_ready_for_update(self, ka_id: str) -> bool:
    """Check if a Knowledge Assistant is ready to be updated.

    Args:
        ka_id: The KA ID (UUID)

    Returns:
        True if the KA is ready for updates (state is ACTIVE), False otherwise
    """
    state = self.ka_get_endpoint_status(ka_id)
    return state == 'ACTIVE'

  def ka_wait_for_ready_status(
    self, ka_id: str, timeout: int = 60, poll_interval: int = 5
  ) -> bool:
    """Wait for a Knowledge Assistant to be ready for updates.

    Args:
        ka_id: The KA ID (UUID)
        timeout: Maximum time to wait in seconds (default: 60)
        poll_interval: Time between status checks in seconds (default: 5)

    Returns:
        True if the KA became ready within the timeout, False otherwise
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
      if self.ka_is_ready_for_update(ka_id):
        logger.info(f'KA {ka_id} is ready (state: ACTIVE)')
        return True

      current_state = self.ka_get_endpoint_status(ka_id)
      logger.info(f'KA {ka_id} state: {current_state}, waiting for ACTIVE...')
      time.sleep(poll_interval)

    logger.warning(f'Timeout waiting for KA {ka_id} to be ready after {timeout} seconds')
    return False

  def ka_update(
    self,
    ka_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    instructions: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Update Knowledge Assistant metadata using the Python SDK.

    Note: Knowledge sources are managed separately using ka_create_source(), ka_update_source(), ka_delete_source().

    Args:
        ka_id: The KA ID (UUID)
        display_name: Optional new display name
        description: Optional new description
        instructions: Optional new instructions

    Returns:
        Updated KA data
    """
    update_fields = []
    ka_updates: Dict[str, Any] = {}

    if display_name is not None:
      sanitized_name = self.sanitize_name(display_name)
      ka_updates['display_name'] = sanitized_name
      update_fields.append('display_name')

    if description is not None:
      ka_updates['description'] = description
      update_fields.append('description')

    if instructions is not None:
      ka_updates['instructions'] = instructions
      update_fields.append('instructions')

    if not update_fields:
      logger.info(f'No fields to update for KA {ka_id}')
      return self.ka_get(ka_id)

    update_mask = ','.join(update_fields)
    logger.info(f'Updating KA {ka_id} with update_mask={update_mask}')

    name = f'knowledge-assistants/{ka_id}'
    ka = KnowledgeAssistant(**ka_updates)
    result = self.w.knowledge_assistants.update_knowledge_assistant(name=name, knowledge_assistant=ka, update_mask=update_mask)
    logger.info(f'KA {ka_id} updated successfully')
    return result.as_dict()

  def ka_sync_sources(self, ka_id: str) -> None:
    """Sync all non-index Knowledge Sources for a Knowledge Assistant using the Python SDK.

    Note: Index sources do not require sync.

    Args:
        ka_id: The KA ID (UUID)
    """
    name = f'knowledge-assistants/{ka_id}'
    self.w.knowledge_assistants.sync_knowledge_sources(name)

  # ---------- Knowledge Source operations ----------

  def ka_list_sources(self, ka_id: str, page_size: int = 100) -> List[Dict[str, Any]]:
    """List all Knowledge Sources under a Knowledge Assistant using the Python SDK.

    Args:
        ka_id: The KA ID (UUID)
        page_size: Number of results per page (max 100)

    Returns:
        List of Knowledge Source objects with structure:
        {
          "id": "...",
          "name": "knowledge-assistants/{ka_id}/knowledge-sources/{ks_id}",
          "display_name": "...",
          "description": "...",
          "source_type": "files|index|file_table",
          "files": {"path": "..."},  # if source_type is "files"
          "index": {"index_name": "...", "text_col": "...", "doc_uri_col": "..."},  # if source_type is "index"
          "file_table": {"table_name": "...", "file_col": "..."},  # if source_type is "file_table"
          "state": "UPDATING|UPDATED|FAILED_UPDATE",
          "create_time": "...",
          "knowledge_cutoff_time": "..."
        }
    """
    parent = f'knowledge-assistants/{ka_id}'
    sources = list(self.w.knowledge_assistants.list_knowledge_sources(parent=parent, page_size=min(page_size, 100)))
    return [s.as_dict() for s in sources]

  def ka_create_source(
    self,
    ka_id: str,
    display_name: str,
    description: str,
    source_type: str,
    files_path: Optional[str] = None,
    index_name: Optional[str] = None,
    index_text_col: Optional[str] = None,
    index_doc_uri_col: Optional[str] = None,
    file_table_name: Optional[str] = None,
    file_table_file_col: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Create a Knowledge Source under a Knowledge Assistant using the Python SDK.

    Args:
        ka_id: The KA ID (UUID)
        display_name: Human-readable display name
        description: Description of the knowledge source
        source_type: Type of source: "files", "index", or "file_table"
        files_path: Path to files (required if source_type is "files")
        index_name: Vector search index name (required if source_type is "index")
        index_text_col: Text column in index (required if source_type is "index")
        index_doc_uri_col: Document URI column in index (required if source_type is "index")
        file_table_name: Table name (required if source_type is "file_table")
        file_table_file_col: File column in table (required if source_type is "file_table")

    Returns:
        Created Knowledge Source data
    """
    ks_kwargs: Dict[str, Any] = {
      'display_name': display_name,
      'description': description,
      'source_type': source_type,
    }

    if source_type == 'files':
      if not files_path:
        raise ValueError("files_path is required when source_type is 'files'")
      ks_kwargs['files'] = FilesSpec(path=files_path)
    elif source_type == 'index':
      if not all([index_name, index_text_col, index_doc_uri_col]):
        raise ValueError("index_name, index_text_col, and index_doc_uri_col are required when source_type is 'index'")
      ks_kwargs['index'] = IndexSpec(index_name=index_name, text_col=index_text_col, doc_uri_col=index_doc_uri_col)
    elif source_type == 'file_table':
      if not all([file_table_name, file_table_file_col]):
        raise ValueError("file_table_name and file_table_file_col are required when source_type is 'file_table'")
      ks_kwargs['file_table'] = FileTableSpec(table_name=file_table_name, file_col=file_table_file_col)
    else:
      raise ValueError(f"Invalid source_type: {source_type}. Must be 'files', 'index', or 'file_table'")

    logger.info(f'Creating knowledge source for KA {ka_id}: {display_name} ({source_type})')
    parent = f'knowledge-assistants/{ka_id}'
    ks = KnowledgeSource(**ks_kwargs)
    result = self.w.knowledge_assistants.create_knowledge_source(parent=parent, knowledge_source=ks)
    return result.as_dict()

  def ka_get_source(self, ka_id: str, source_id: str) -> Optional[Dict[str, Any]]:
    """Get a Knowledge Source by ID using the Python SDK.

    Args:
        ka_id: The KA ID (UUID)
        source_id: The Knowledge Source ID (UUID)

    Returns:
        Knowledge Source data or None if not found
    """
    try:
      name = f'knowledge-assistants/{ka_id}/knowledge-sources/{source_id}'
      result = self.w.knowledge_assistants.get_knowledge_source(name)
      return result.as_dict()
    except Exception as e:
      error_str = str(e).lower()
      if 'does not exist' in error_str or 'not found' in error_str:
        return None
      raise

  def ka_update_source(
    self,
    ka_id: str,
    source_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Update a Knowledge Source using the Python SDK.

    Note: Only display_name and description can be updated. Source type and configuration cannot be changed.

    Args:
        ka_id: The KA ID (UUID)
        source_id: The Knowledge Source ID (UUID)
        display_name: Optional new display name
        description: Optional new description

    Returns:
        Updated Knowledge Source data
    """
    update_fields = []
    ks_kwargs: Dict[str, Any] = {}

    if display_name is not None:
      ks_kwargs['display_name'] = display_name
      update_fields.append('display_name')

    if description is not None:
      ks_kwargs['description'] = description
      update_fields.append('description')

    if not update_fields:
      logger.info(f'No fields to update for source {source_id}')
      return self.ka_get_source(ka_id, source_id)

    update_mask = ','.join(update_fields)
    name = f'knowledge-assistants/{ka_id}/knowledge-sources/{source_id}'
    ks = KnowledgeSource(**ks_kwargs)
    result = self.w.knowledge_assistants.update_knowledge_source(name=name, knowledge_source=ks, update_mask=update_mask)
    return result.as_dict()

  def ka_delete_source(self, ka_id: str, source_id: str) -> None:
    """Delete a Knowledge Source using the Python SDK.

    Args:
        ka_id: The KA ID (UUID)
        source_id: The Knowledge Source ID (UUID)
    """
    name = f'knowledge-assistants/{ka_id}/knowledge-sources/{source_id}'
    self.w.knowledge_assistants.delete_knowledge_source(name)
    logger.info(f'Deleted knowledge source {source_id} from KA {ka_id}')

  def ka_reconcile_model(self, tile_id: str) -> None:
    """Reconcile KA to latest model (deprecated - may not work with new API)."""
    self._patch(f'/api/2.0/knowledge-assistants/{tile_id}/reconcile-model', {})

  def share(self, tile_id: str, changes: List[Dict[str, Any]]) -> None:
    """Share KA with specified permissions.

    Args:
        tile_id: The KA tile ID
        changes: List of permission changes, each containing:
            - principal: User/group/service principal (e.g., "users:email@company.com", "groups:data-team")
            - add: List of permissions to grant (can be Permission enum values or strings)
            - remove: List of permissions to revoke (can be Permission enum values or strings)

    Example:
        ka_manager.share(
            tile_id,
            changes=[
                {
                    "principal": "users:john.doe@company.com",
                    "add": [Permission.CAN_READ, Permission.CAN_RUN],
                    "remove": []
                },
                {
                    "principal": "groups:data-scientists",
                    "add": ["CAN_READ"],  # Can also use strings
                    "remove": ["CAN_MANAGE"]
                }
            ]
        )

    Available permissions:
        - Permission.CAN_READ: View/read access
        - Permission.CAN_WRITE: Write/edit access
        - Permission.CAN_RUN: Execute/run access
        - Permission.CAN_MANAGE: Full management access including ACLs
        - Permission.CAN_VIEW: View metadata only
    """
    # Convert Permission enums to strings in the changes
    processed_changes = []
    for change in changes:
      processed_change = {
        'principal': change['principal'],
        'add': [p.value if isinstance(p, Permission) else p for p in change.get('add', [])],
        'remove': [p.value if isinstance(p, Permission) else p for p in change.get('remove', [])],
      }
      processed_changes.append(processed_change)

    self._post(
      f'/api/2.0/knowledge-assistants/{tile_id}/share',
      {'changes': processed_changes},
    )

  # ---------- Examples Management ----------

  def ka_create_example(
    self, tile_id: str, question: str, guidelines: Optional[List[str]] = None
  ) -> Dict[str, Any]:
    """Create an example question for the Knowledge Assistant.

    Args:
        tile_id: The KA tile ID
        question: The example question
        guidelines: Optional list of guidelines for answering the question

    Returns:
        Created example with example_id
    """
    payload = {'tile_id': tile_id, 'question': question}
    if guidelines:
      payload['guidelines'] = guidelines

    return self._post(f'/api/2.0/knowledge-assistants/{tile_id}/examples', payload)

  def ka_list_examples(
    self, tile_id: str, page_size: int = 100, page_token: Optional[str] = None
  ) -> 'KnowledgeAssistantListExamplesResponseDict':
    """List all examples for a Knowledge Assistant.

    Args:
        tile_id: The KA tile ID
        page_size: Number of examples per page
        page_token: Token for pagination

    Returns:
        Dictionary with structure:
        {
          "examples": [{"example_id", "question", "guidelines": [...], "feedback_records": [...]}, ...],
          "tile_id": "...",
          "next_page_token": "..." (optional)
        }
    """
    params = {'page_size': page_size}
    if page_token:
      params['page_token'] = page_token

    return self._get(f'/api/2.0/knowledge-assistants/{tile_id}/examples', params=params)

  def ka_delete_example(self, tile_id: str, example_id: str) -> None:
    """Delete an example from the Knowledge Assistant."""
    self._delete(f'/api/2.0/knowledge-assistants/{tile_id}/examples/{example_id}')

  def ka_add_examples_batch(
    self, tile_id: str, questions: List[Dict[str, Any]]
  ) -> List[Dict[str, Any]]:
    """Add multiple example questions to the Knowledge Assistant in parallel using threads.

    Args:
        tile_id: The KA tile ID
        questions: List of question dictionaries, each containing:
            - 'question': The question text (required)
            - 'guideline': Optional guideline for answering the question

    Returns:
        List of created examples
    """
    created_examples = []

    def create_example_with_progress(question_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
      """Helper function to create an example and print progress."""
      question_text = question_item.get('question', '')
      guideline = question_item.get('guideline')
      # Convert single guideline to list format expected by API
      guidelines = [guideline] if guideline else None

      if not question_text:
        return None

      try:
        example = self.ka_create_example(tile_id, question_text, guidelines)
        logger.info(f'Added example: {question_text[:50]}...')
        return example
      except Exception as e:
        logger.error(f"Failed to add example '{question_text[:50]}...': {e}")
        return None

    # Use ThreadPoolExecutor to run example creation in parallel
    # Limit to 10 concurrent threads to avoid overwhelming the API
    max_workers = min(2, len(questions))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
      # Submit all tasks
      future_to_question = {executor.submit(create_example_with_progress, q): q for q in questions}

      # Collect results as they complete
      for future in as_completed(future_to_question):
        result = future.result()
        if result is not None:
          created_examples.append(result)

    return created_examples

  def ka_list_evaluation_runs(
    self, tile_id: str, page_size: int = 100, page_token: Optional[str] = None
  ) -> 'ListEvaluationRunsResponseDict':
    """List all evaluation runs for a Knowledge Assistant.

    Args:
        tile_id: The KA tile ID
        page_size: Number of evaluation runs per page
        page_token: Token for pagination

    Returns:
        Dictionary with structure:
        {
          "evaluation_runs": [
            {
              "mlflow_run_id": "...",
              "tile_id": "...",
              "name": "...",
              "created_timestamp_ms": 1234567890,
              "last_updated_timestamp_ms": 1234567890
            },
            ...
          ],
          "next_page_token": "..." (optional)
        }
    """
    params = {'page_size': page_size}
    if page_token:
      params['page_token'] = page_token

    return self._get(f'/api/2.0/tiles/{tile_id}/evaluation-runs', params=params)

  # ---------- Helper methods ----------

  @staticmethod
  def ka_get_knowledge_sources_from_volumes(
    volume_paths: List[Tuple[str, Optional[str]]],
  ) -> List[Dict[str, Any]]:
    """Convert volume paths with descriptions into knowledge source config dictionaries.

    Args:
        volume_paths: List of tuples (volume_path, description) where:
            - volume_path: Path to UC volume (e.g., '/Volumes/catalog/schema/docs')
            - description: Optional description for the source

    Returns:
        List of knowledge source config dictionaries for use with ka_create_or_update()

    Example:
        >>> paths = [
        ...     ('/Volumes/main/default/docs', 'Technical documentation'),
        ...     ('/Volumes/main/default/guides', 'User guides'),
        ...     ('/Volumes/main/default/api', None)  # No description
        ... ]
        >>> sources = AgentBricksManager.ka_get_knowledge_sources_from_volumes(paths)
        >>> # Returns list of config dicts for ka_create_or_update()
    """
    knowledge_sources = []

    for idx, (volume_path, description) in enumerate(volume_paths):
      # Extract a source name from the path (last component or use index)
      path_parts = volume_path.rstrip('/').split('/')
      if len(path_parts) > 0:
        # Use the last folder name as the source name
        source_name = path_parts[-1]
      else:
        # Fallback to indexed name
        source_name = f'source_{idx + 1}'

      # Ensure source name is valid (alphanumeric, hyphens, underscores)
      source_name = source_name.replace(' ', '_').replace('.', '_')

      # Build the knowledge source config dictionary for new API
      knowledge_source = {
        'display_name': source_name,
        'description': description or f'Files from {volume_path}',
        'source_type': 'files',
        'files_path': volume_path,
      }

      knowledge_sources.append(knowledge_source)

    return knowledge_sources

  # ---------- Discovery & Listing ----------

  def ka_list(self, page_size: int = 100) -> List[Dict[str, Any]]:
    """List all Knowledge Assistants in the workspace using the Python SDK.

    Args:
        page_size: Number of results per page (max 100).

    Returns:
        List of Knowledge Assistant objects with structure:
        {
          "id": "...",
          "name": "knowledge-assistants/{id}",
          "display_name": "...",
          "description": "...",
          "instructions": "...",
          "endpoint_name": "...",
          "state": "CREATING|ACTIVE|FAILED",
          "create_time": "...",
          "creator": "...",
          "experiment_id": "...",
          "error_info": "..."  # Only when state is FAILED
        }
    """
    kas = list(self.w.knowledge_assistants.list_knowledge_assistants(page_size=min(page_size, 100)))
    return [ka.as_dict() for ka in kas]

  def list_all_agent_bricks(
    self, tile_type: Optional[TileType] = None, page_size: int = 100
  ) -> List[Dict[str, Any]]:
    """List all agent bricks (tiles) in the workspace.

    Args:
        tile_type: Specific tile type to filter for. If None, returns all tiles.
        page_size: Number of results per page.

    Returns:
        List of Tile objects.
    """
    all_tiles = []

    # Build filter query
    if tile_type:
      filter_q = f'tile_type={tile_type.name}'
    else:
      # No filter means get all tiles
      filter_q = None

    page_token = None

    while True:
      params = {'page_size': page_size}
      if filter_q:
        params['filter'] = filter_q
      if page_token:
        params['page_token'] = page_token

      resp = self._get('/api/2.0/tiles', params=params)

      # Add tiles from this page
      for tile in resp.get('tiles', []):
        # If we're filtering by type, double-check it matches
        if tile_type:
          tile_type_value = tile.get('tile_type')
          # Check both numeric and string representation
          if tile_type_value == tile_type.value or tile_type_value == tile_type.name:
            all_tiles.append(tile)
        else:
          # No filter, add all tiles
          all_tiles.append(tile)

      # Check for next page
      page_token = resp.get('next_page_token')
      if not page_token:
        break

    return all_tiles

  def find_by_name(self, display_name: str) -> Optional[KAIds]:
    """Resolve a KA by exact display_name using the Python SDK.

    Args:
        display_name: The display name of the KA to find

    Returns:
        KAIds with tile_id (id) and name (display_name), or None if not found
    """
    for ka in self.w.knowledge_assistants.list_knowledge_assistants():
      if ka.display_name == display_name:
        return KAIds(tile_id=ka.id, name=display_name)
    return None

  def mas_find_by_name(self, name: str) -> Optional[MASIds]:
    """Resolve a MAS by exact display name using /2.0/tiles listing + filter.
    We search tile_type=MAS and then pick exact name match client-side.
    """
    filter_q = f'name_contains={name}&&tile_type=MAS'
    path = '/api/2.0/tiles'
    page_token = None
    while True:
      params = {'filter': filter_q}
      if page_token:
        params['page_token'] = page_token
      resp = self._get(path, params=params)
      for t in resp.get('tiles', []):
        if t.get('name') == name:
          return MASIds(tile_id=t['tile_id'], name=name)
      page_token = resp.get('next_page_token')
      if not page_token:
        break
    return None

  def genie_find_by_name(self, display_name: str) -> Optional[GenieIds]:
    """Resolve a Genie space by exact display name using the Python SDK.
    """
    try:
      page_token = None
      while True:
        response = self.w.genie.list_spaces(page_token=page_token)
        for space in response.spaces or []:
          if space.title == display_name:
            return GenieIds(space_id=space.space_id, display_name=display_name)
        page_token = response.next_page_token
        if not page_token:
          break
    except Exception as e:
      logger.warning(f'Failed to list Genie spaces: {e}')
    return None

  # ---------- Polling (optional helpers) ----------

  def ka_wait_until_ready(
    self, ka_id: str, timeout_s: Optional[int] = None, poll_s: Optional[float] = None
  ) -> Dict[str, Any]:
    """Wait until the KA is ready for updates (not in CREATING state).
    Returns the KA object once ready or after timeout.
    """
    timeout_s = timeout_s or self.default_timeout_s
    poll_s = poll_s or self.default_poll_s
    deadline = time.time() + timeout_s

    while True:
      ka = self.ka_get(ka_id)
      if not ka:
        if time.time() >= deadline:
          raise TimeoutError(f'KA {ka_id} was not found within {timeout_s} seconds')
        time.sleep(poll_s)
        continue

      state = ka.get('state')

      # Ready states are ACTIVE or FAILED
      # Not ready state is CREATING
      if state and state != 'CREATING':
        return ka

      if time.time() >= deadline:
        return ka  # return last seen state even if still creating

      time.sleep(poll_s)

  def ka_wait_until_endpoint_online(
    self, ka_id: str, timeout_s: Optional[int] = None, poll_s: Optional[float] = None
  ) -> Dict[str, Any]:
    """Wait for KA state to be ACTIVE (equivalent to old ONLINE status).
    Returns the KA object.
    """
    timeout_s = timeout_s or self.default_timeout_s
    poll_s = poll_s or self.default_poll_s
    deadline = time.time() + timeout_s
    start_time = time.time()
    last_state = None
    ka = None

    while True:
      try:
        ka = self.ka_get(ka_id)
        if not ka:
          elapsed = int(time.time() - start_time)
          if elapsed < 60:
            logger.debug(f'[{elapsed}s] KA not yet available, waiting for propagation...')
            time.sleep(poll_s)
            continue
          else:
            raise TimeoutError(f'KA {ka_id} was not found within {timeout_s} seconds')

        state = ka.get('state')

        # Log state changes
        if state != last_state:
          elapsed = int(time.time() - start_time)
          logger.info(f'[{elapsed}s] KA state changed: {last_state} -> {state}')
          last_state = state

        if state == 'ACTIVE':
          return ka
        elif state == 'FAILED':
          error_info = ka.get('error_info', 'Unknown error')
          raise Exception(f'KA {ka_id} failed: {error_info}')
      except TimeoutError:
        raise
      except Exception as e:
        if 'failed' in str(e).lower():
          raise
        # Handle case where KA is not immediately available after creation
        elapsed = int(time.time() - start_time)
        if 'does not exist' in str(e) and elapsed < 60:
          logger.debug(f'[{elapsed}s] KA not yet available, waiting for propagation...')
        else:
          raise

      if time.time() >= deadline:
        elapsed = int(time.time() - start_time)
        logger.warning(f'[{elapsed}s] Timeout reached waiting for ACTIVE state. Final state: {last_state}')
        if ka:
          return ka
        else:
          raise TimeoutError(f'KA {ka_id} was not found within {timeout_s} seconds')
      time.sleep(poll_s)

  # ---------- MAS-specific operations ----------

  def mas_create(
    self,
    name: str,
    agents: List[Dict[str, Any]],
    description: Optional[str] = None,
    instructions: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Create a Multi-Agent Supervisor with specified agents.
    note: output_path is deprecated and removed.

    Args:
        name: Name for the MAS
        agents: List of agent configurations (BaseAgent format)
                Each agent should have: name, description, agent_type, and one of:
                - genie_space: {"id": "space_id"}
                - serving_endpoint: {"name": "endpoint_name"}
                - app: {"name": "app_name"}
                - unity_catalog_function: {"uc_path": {"catalog": "...", "schema": "...", "name": "..."}}
        description: Optional description
        instructions: Optional instructions for the MAS

    Returns:
        MAS creation response with tile info

    Example:
        >>> agents = [
        ...     {
        ...         "name": "Data Agent",
        ...         "description": "Analyzes data",
        ...         "agent_type": "genie",
        ...         "genie_space": {"id": "genie-space-123"}
        ...     },
        ...     {
        ...         "name": "KA Agent",
        ...         "description": "Answers questions",
        ...         "agent_type": "ka",
        ...         "serving_endpoint": {"name": "ka-endpoint-456"}
        ...     }
        ... ]
        >>> manager.mas_create("My MAS", agents, description="Multi-agent system")
    """
    payload = {'name': self.sanitize_name(name), 'agents': agents}

    if description:
      payload['description'] = description
    if instructions:
      payload['instructions'] = instructions

    logger.info(f'Creating MAS with name={name}, {len(agents)} agents')
    return self._post('/api/2.0/multi-agent-supervisors', payload)

  def mas_update(
    self,
    tile_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    instructions: Optional[str] = None,
    agents: Optional[List[Dict[str, Any]]] = None,
  ) -> Dict[str, Any]:
    """Update a Multi-Agent Supervisor.

    Args:
        tile_id: The MAS tile ID
        name: Optional new name
        description: Optional new description
        instructions: Optional new instructions
        agents: Optional new list of agents

    Returns:
        Updated MAS data
    """
    payload = {'tile_id': tile_id}

    if name:
      payload['name'] = self.sanitize_name(name)
    if description:
      payload['description'] = description
    if instructions:
      payload['instructions'] = instructions
    if agents:
      payload['agents'] = agents

    logger.info(f'Updating MAS {tile_id}')
    return self._patch(f'/api/2.0/multi-agent-supervisors/{tile_id}', payload)

  def mas_get(self, tile_id: str) -> Optional['MultiAgentSupervisorResponseDict']:
    """Get MAS by tile_id using the MAS-specific endpoint.

    Returns:
        MAS data dictionary with structure:
        {
          "multi_agent_supervisor": {
            "tile": {"tile_id", "name", "description", "instructions", ...},
            "agents": [{"name", "description", "agent_type", "genie_space": {...}, ...}, ...],
            "status": {"endpoint_status": "ONLINE|OFFLINE|PROVISIONING|NOT_READY"}
          }
        }
        Returns None if not found or doesn't exist.
    """
    try:
      return self._get(f'/api/2.0/multi-agent-supervisors/{tile_id}')
    except Exception as e:
      # Handle NotFound errors gracefully
      if 'does not exist' in str(e).lower() or 'not found' in str(e).lower():
        return None
      # Re-raise other errors
      raise

  def mas_get_endpoint_status(self, tile_id: str) -> Optional[str]:
    """Get the endpoint status of a Multi-Agent Supervisor.

    Args:
        tile_id: The MAS tile ID

    Returns:
        The endpoint status string (ONLINE, OFFLINE, PROVISIONING, NOT_READY) or None
    """
    mas = self.mas_get(tile_id)
    if not mas:
      return None

    return mas.get('multi_agent_supervisor', {}).get('status', {}).get('endpoint_status')

  # ---------- Genie Space operations ----------

  def genie_get(self, space_id: str) -> Optional[Dict[str, Any]]:
    """Get Genie space by ID.

    Note: Uses raw API because the SDK's GenieSpace dataclass doesn't include
    warehouse_id and other fields needed by the application.

    Args:
        space_id: The Genie space ID

    Returns:
        Dictionary with Genie space data:
        {
          "space_id": "...",
          "id": "...",  # Same as space_id
          "display_name": "...",
          "description": "...",
          "warehouse_id": "...",
          "table_identifiers": [...],
          ...
        }
        Returns None if not found or doesn't exist.
        Note: Databricks returns PERMISSION_DENIED for non-existent spaces.
    """
    try:
      result = self._get(f'/api/2.0/data-rooms/{space_id}')
      # Ensure both id and space_id are present for compatibility
      if 'id' not in result:
        result['id'] = result.get('space_id', space_id)
      if 'space_id' not in result:
        result['space_id'] = result.get('id', space_id)
      # Add display_name alias for title if present
      if 'display_name' not in result and 'title' in result:
        result['display_name'] = result['title']
      return result
    except Exception as e:
      error_str = str(e).lower()
      # Databricks returns PERMISSION_DENIED for non-existent spaces
      if 'does not exist' in error_str or 'not found' in error_str or 'permission' in error_str:
        return None
      raise

  def genie_create(
    self,
    display_name: str,
    warehouse_id: str,
    table_identifiers: List[str],
    description: Optional[str] = None,
    parent_folder_path: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
    create_dir: bool = True,
    run_as_type: str = 'VIEWER',
  ) -> Dict[str, Any]:
    """Create a Genie space.

    Note: The SDK create_space method doesn't support table_identifiers directly,
    so we still use the raw API for creation to include all needed fields.

    Args:
        display_name: Display name for the space
        warehouse_id: Warehouse ID to use
        table_identifiers: List of full table names (e.g., ["catalog.schema.table"])
        description: Optional space description
        parent_folder_path: Optional workspace folder path (e.g., "/Users/user@company.com/demos/_genie_spaces")
        parent_folder_id: Optional parent folder ID (use this if you already have the ID)
        create_dir: Whether to create the parent folder if it doesn't exist (default: True)
        run_as_type: Run as type (default: "VIEWER")

    Returns:
        Dictionary with space data including 'id' and 'space_id'

    Example:
        >>> # Using folder path
        >>> manager.genie_create(
        ...     display_name="Marketing Campaign Analysis",
        ...     warehouse_id="warehouse-123",
        ...     table_identifiers=["main.default.campaigns", "main.default.contacts"],
        ...     description="Analyze marketing campaign effectiveness",
        ...     parent_folder_path="/Users/user@company.com/demos/_genie_spaces"
        ... )
    """
    # Validate folder parameters
    if parent_folder_path and parent_folder_id:
      raise ValueError('Cannot specify both parent_folder_path and parent_folder_id')

    if parent_folder_path and '/' not in parent_folder_path:
      raise ValueError(f"parent_folder_path must contain '/' (got: {parent_folder_path})")

    room_payload = {
      'display_name': display_name,
      'warehouse_id': warehouse_id,
      'table_identifiers': table_identifiers,
      'run_as_type': run_as_type,
    }

    if description:
      room_payload['description'] = description

    # Resolve parent folder
    if parent_folder_path:
      # Create directory if requested
      if create_dir:
        try:
          self.w.workspace.mkdirs(parent_folder_path)
        except Exception as e:
          logger.warning(f'Could not create directory {parent_folder_path}: {e}')
          raise e

      # Get folder ID from path using SDK
      try:
        folder_status = self.w.workspace.get_status(parent_folder_path)
        parent_folder_id = str(folder_status.object_id)
      except Exception as e:
        raise ValueError(f"Failed to get folder ID for path '{parent_folder_path}': {str(e)}")

    if parent_folder_id:
      room_payload['parent_folder'] = f'folders/{parent_folder_id}'

    # Use raw API as SDK doesn't support table_identifiers in create_space
    result = self._post('/api/2.0/data-rooms/', room_payload)
    # Normalize response
    if 'space_id' in result and 'id' not in result:
      result['id'] = result['space_id']
    elif 'id' in result and 'space_id' not in result:
      result['space_id'] = result['id']
    return result

  def genie_update(
    self,
    space_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    table_identifiers: Optional[List[str]] = None,
    sample_questions: Optional[List[str]] = None,
  ) -> Dict[str, Any]:
    """Update a Genie space.

    Note: SDK update_space doesn't support all our use cases (table_identifiers, etc.),
    so we use raw API for the update itself.

    Args:
        space_id: The Genie space ID
        display_name: Optional new display name
        description: Optional new description
        warehouse_id: Optional new warehouse ID
        table_identifiers: Optional new list of table identifiers
        sample_questions: Optional list of sample questions (will replace all existing questions)

    Returns:
        Updated Genie space data

    Example:
        >>> # Update display name and description
        >>> manager.genie_update(
        ...     space_id,
        ...     display_name="Updated Space Name",
        ...     description="New description"
        ... )
        >>>
        >>> # Update sample questions (replaces all existing)
        >>> manager.genie_update(
        ...     space_id,
        ...     sample_questions=["What is revenue?", "How many customers?"]
        ... )
    """
    # Get current space to preserve required fields
    current_space = self.genie_get(space_id)
    if not current_space:
      raise ValueError(f'Genie space {space_id} not found')

    # Build update payload with current values as defaults
    update_payload = {
      'id': space_id,
      'space_id': space_id,
      'display_name': display_name or current_space.get('display_name') or current_space.get('title'),
      'warehouse_id': warehouse_id or current_space.get('warehouse_id'),
    }

    # Only include table_identifiers if provided (not from genie_get as SDK doesn't return it)
    if table_identifiers is not None:
      update_payload['table_identifiers'] = table_identifiers

    # Add optional fields if provided
    if description is not None:
      update_payload['description'] = description
    elif current_space.get('description'):
      update_payload['description'] = current_space['description']

    # Update space metadata first
    result = self._patch(f'/api/2.0/data-rooms/{space_id}', update_payload)

    # Update sample questions separately if provided (using curated-questions endpoint)
    if sample_questions is not None:
      self.genie_update_sample_questions(space_id, sample_questions)

    return result

  def genie_update_sample_questions(self, space_id: str, questions: List[str]) -> Dict[str, Any]:
    """Update sample questions for a Genie space by replacing all existing questions.

    This uses the batch-actions endpoint to delete old questions and create new ones.

    Args:
        space_id: The Genie space ID
        questions: List of question texts (will replace ALL existing sample questions)

    Returns:
        Response from the batch-actions endpoint

    Example:
        >>> manager.genie_update_sample_questions(
        ...     space_id,
        ...     ["What is revenue?", "How many customers?", "Show me trends"]
        ... )
    """
    # Get existing sample questions to delete them
    existing_questions = self.genie_list_questions(space_id, question_type='SAMPLE_QUESTION')
    existing_question_ids = [
      q.get('curated_question_id') or q.get('id')
      for q in existing_questions.get('curated_questions', [])
      if q.get('curated_question_id') or q.get('id')
    ]

    # Build actions array: first delete all existing, then create new ones
    actions = []

    # Add DELETE actions for existing questions
    for question_id in existing_question_ids:
      actions.append({'action_type': 'DELETE', 'curated_question_id': question_id})

    # Add CREATE actions for new questions
    for question_text in questions:
      actions.append({
        'action_type': 'CREATE',
        'curated_question': {
          'data_room_id': space_id,
          'question_text': question_text,
          'question_type': 'SAMPLE_QUESTION',
        },
      })

    # Execute batch actions
    payload = {'actions': actions}
    return self._post(f'/api/2.0/data-rooms/{space_id}/curated-questions/batch-actions', payload)

  def genie_delete(self, space_id: str) -> None:
    """Delete (trash) a Genie space using the Python SDK.

    Args:
        space_id: The Genie space ID to delete
    """
    self.w.genie.trash_space(space_id)

  def genie_list_questions(
    self, space_id: str, question_type: str = 'SAMPLE_QUESTION'
  ) -> 'GenieListQuestionsResponseDict':
    """List curated questions for a Genie space.

    Args:
        space_id: The Genie space ID
        question_type: Type of questions to list (default: SAMPLE_QUESTION)

    Returns:
        Dictionary with structure:
        {
          "curated_questions": [
            {"question_id", "data_space_id", "question_text", "question_type", "answer_text", ...},
            ...
          ]
        }
    """
    return self._get(
      f'/api/2.0/data-rooms/{space_id}/curated-questions', params={'question_type': question_type}
    )

  def genie_list_instructions(self, space_id: str) -> 'GenieListInstructionsResponseDict':
    """List all instructions for a Genie space.

    Args:
        space_id: The Genie space ID

    Returns:
        Dictionary with structure:
        {
          "instructions": [
            {
              "instruction_id": "...",
              "title": "...",
              "content": "...",
              "instruction_type": "TEXT_INSTRUCTION|SQL_INSTRUCTION|CERTIFIED_ANSWER"
            },
            ...
          ]
        }

    Example:
        >>> # Get all instructions (text notes, SQL examples, certified answers)
        >>> instructions = manager.genie_list_instructions(space_id)
        >>> for instr in instructions['instructions']:
        ...     print(f"{instr['instruction_type']}: {instr['title']}")
    """
    return self._get(f'/api/2.0/data-rooms/{space_id}/instructions')

  def genie_add_sample_questions_batch(self, space_id: str, questions: List[str]) -> Dict[str, Any]:
    """Add multiple sample questions to a Genie space using batch actions.

    Args:
        space_id: The Genie space ID
        questions: List of question texts to add

    Returns:
        Batch action response

    Example:
        >>> questions = ["What is the total revenue?", "How many customers?"]
        >>> manager.genie_add_sample_questions_batch(space_id, questions)
    """
    actions = [
      {
        'action_type': 'CREATE',
        'curated_question': {
          'data_space_id': space_id,
          'question_text': q,
          'question_type': 'SAMPLE_QUESTION',
        },
      }
      for q in questions
    ]
    return self._post(
      f'/api/2.0/data-rooms/{space_id}/curated-questions/batch-actions', {'actions': actions}
    )

  def genie_add_curated_question(
    self, space_id: str, question_text: str, question_type: str, answer_text: Optional[str] = None
  ) -> Dict[str, Any]:
    """Low-level method to add a curated question to a Genie space.

    Use the specific methods instead: genie_add_sample_question(), genie_add_benchmark()

    Args:
        space_id: The Genie space ID
        question_text: The question text
        question_type: Type of question (SAMPLE_QUESTION or BENCHMARK)
        answer_text: Optional answer text (required for BENCHMARK type)

    Returns:
        Created curated question
    """
    curated_question = {
      'data_space_id': space_id,
      'question_text': question_text,
      'question_type': question_type,
      'is_deprecated': False,
    }
    if answer_text:
      curated_question['answer_text'] = answer_text

    payload = {'curated_question': curated_question, 'data_space_id': space_id}
    return self._post(f'/api/2.0/data-rooms/{space_id}/curated-questions', payload)

  def genie_add_sample_question(self, space_id: str, question_text: str) -> Dict[str, Any]:
    """Add a sample question to a Genie space.

    Args:
        space_id: The Genie space ID
        question_text: The sample question

    Returns:
        Created sample question

    Example:
        >>> manager.genie_add_sample_question(space_id, "What is the total revenue?")
    """
    return self.genie_add_curated_question(space_id, question_text, 'SAMPLE_QUESTION')

  def genie_add_instruction(
    self, space_id: str, title: str, content: str, instruction_type: str
  ) -> Dict[str, Any]:
    """Low-level method to add an instruction to a Genie space.

    Use the specific methods instead: genie_add_text_instruction(), genie_add_sql_instruction(), genie_add_sql_function()

    Args:
        space_id: The Genie space ID
        title: Title of the instruction
        content: Content of the instruction
        instruction_type: Type (TEXT_INSTRUCTION, SQL_INSTRUCTION, or CERTIFIED_ANSWER)

    Returns:
        Created instruction
    """
    payload = {'title': title, 'content': content, 'instruction_type': instruction_type}
    return self._post(f'/api/2.0/data-rooms/{space_id}/instructions', payload)

  def genie_add_text_instruction(
    self, space_id: str, content: str, title: str = 'Notes'
  ) -> Dict[str, Any]:
    """Add general text instruction/notes to a Genie space.

    Args:
        space_id: The Genie space ID
        content: Instruction content (general notes, context, guidelines)
        title: Title for the instruction (default: "Notes")

    Returns:
        Created instruction

    Example:
        >>> manager.genie_add_text_instruction(
        ...     space_id,
        ...     "If customer asks for forecast, use ai_forecast function.\\n"
        ...     "The mailing_list column contains all contact_ids."
        ... )
    """
    return self.genie_add_instruction(space_id, title, content, 'TEXT_INSTRUCTION')

  def genie_add_sql_instruction(self, space_id: str, title: str, content: str) -> Dict[str, Any]:
    """Add a SQL query example instruction to a Genie space.

    Args:
        space_id: The Genie space ID
        title: Description of what the SQL query does
        content: The SQL query

    Returns:
        Created instruction

    Example:
        >>> manager.genie_add_sql_instruction(
        ...     space_id,
        ...     "Compute rolling metrics",
        ...     "SELECT date, SUM(clicks) OVER (ORDER BY date...) FROM metrics"
        ... )
    """
    return self.genie_add_instruction(space_id, title, content, 'SQL_INSTRUCTION')

  def genie_add_sql_function(self, space_id: str, function_name: str) -> Dict[str, Any]:
    """Add a SQL function (certified answer) to a Genie space.

    Args:
        space_id: The Genie space ID
        function_name: Full SQL function name (e.g., "catalog.schema.function_name")

    Returns:
        Created instruction

    Example:
        >>> manager.genie_add_sql_function(space_id, "main.default.get_highest_ctr")
    """
    return self.genie_add_instruction(space_id, 'SQL Function', function_name, 'CERTIFIED_ANSWER')

  def genie_add_sql_instructions_batch(
    self, space_id: str, sql_instructions: List[Dict[str, str]]
  ) -> List[Dict[str, Any]]:
    """Add multiple SQL query example instructions to a Genie space.

    Args:
        space_id: The Genie space ID
        sql_instructions: List of SQL instructions, each with 'title' and 'content' keys

    Returns:
        List of created instructions

    Example:
        >>> sql_instructions = [
        ...     {
        ...         "title": "Compute rolling metrics",
        ...         "content": "SELECT date, SUM(clicks) OVER (ORDER BY date...) FROM metrics"
        ...     },
        ...     {
        ...         "title": "Campaigns with highest CTR",
        ...         "content": "SELECT campaign_id, SUM(clicks)/SUM(delivered) as ctr FROM events..."
        ...     }
        ... ]
        >>> manager.genie_add_sql_instructions_batch(space_id, sql_instructions)
    """
    results = []
    for sql_instr in sql_instructions:
      try:
        result = self.genie_add_sql_instruction(space_id, sql_instr['title'], sql_instr['content'])
        results.append(result)
        logger.info(f'Added SQL instruction: {sql_instr["title"][:50]}...')
      except Exception as e:
        logger.error(f"Failed to add SQL instruction '{sql_instr['title']}': {e}")
    return results

  def genie_add_sql_functions_batch(
    self, space_id: str, function_names: List[str]
  ) -> List[Dict[str, Any]]:
    """Add multiple SQL functions (certified answers) to a Genie space.

    Args:
        space_id: The Genie space ID
        function_names: List of full SQL function names (e.g., ["catalog.schema.func1", "catalog.schema.func2"])

    Returns:
        List of created instructions

    Example:
        >>> function_names = [
        ...     "main.default.get_highest_ctr",
        ...     "main.default.calculate_revenue"
        ... ]
        >>> manager.genie_add_sql_functions_batch(space_id, function_names)
    """
    results = []
    for func_name in function_names:
      try:
        result = self.genie_add_sql_function(space_id, func_name)
        results.append(result)
        logger.info(f'Added SQL function: {func_name}')
      except Exception as e:
        logger.error(f"Failed to add SQL function '{func_name}': {e}")
    return results

  def genie_add_benchmark(
    self, space_id: str, question_text: str, answer_text: str
  ) -> Dict[str, Any]:
    """Add a benchmark question (question with expected answer) to a Genie space.

    Args:
        space_id: The Genie space ID
        question_text: The benchmark question
        answer_text: The expected answer (can be SQL query or text result)

    Returns:
        Created benchmark question

    Example:
        >>> # Benchmark with SQL query as answer
        >>> manager.genie_add_benchmark(
        ...     space_id,
        ...     "Which campaign has the highest click through rate?",
        ...     "SELECT * FROM get_highest_ctr()"
        ... )
        >>>
        >>> # Another benchmark
        >>> manager.genie_add_benchmark(
        ...     space_id,
        ...     "Which campaign had the most clicks?",
        ...     "SELECT campaign_id, COUNT(*) as total_clicks FROM events "
        ...     "WHERE event_type = 'click' GROUP BY campaign_id ORDER BY total_clicks DESC LIMIT 1"
        ... )
    """
    return self.genie_add_curated_question(space_id, question_text, 'BENCHMARK', answer_text)

  def genie_add_benchmarks_batch(
    self, space_id: str, benchmarks: List[Dict[str, str]]
  ) -> List[Dict[str, Any]]:
    """Add multiple benchmark questions to a Genie space.

    Args:
        space_id: The Genie space ID
        benchmarks: List of benchmarks, each with 'question_text' and 'answer_text' keys

    Returns:
        List of created benchmarks

    Example:
        >>> benchmarks = [
        ...     {
        ...         "question_text": "Which campaign has the highest click through rate?",
        ...         "answer_text": "SELECT * FROM get_highest_ctr()"
        ...     },
        ...     {
        ...         "question_text": "Which campaign had the most clicks?",
        ...         "answer_text": "SELECT campaign_id, COUNT(*) FROM events GROUP BY campaign_id"
        ...     }
        ... ]
        >>> manager.genie_add_benchmarks_batch(space_id, benchmarks)
    """
    results = []
    for benchmark in benchmarks:
      try:
        result = self.genie_add_benchmark(
          space_id, benchmark['question_text'], benchmark['answer_text']
        )
        results.append(result)
        logger.info(f'Added benchmark: {benchmark["question_text"][:50]}...')
      except Exception as e:
        logger.error(f"Failed to add benchmark '{benchmark['question_text'][:50]}...': {e}")
    return results

  # ---------- MAS Examples Management ----------

  def mas_create_example(
    self, tile_id: str, question: str, guidelines: Optional[List[str]] = None
  ) -> Dict[str, Any]:
    """Create an example question for the Multi-Agent Supervisor.

    Args:
        tile_id: The MAS tile ID
        question: The example question
        guidelines: Optional list of guidelines for answering

    Returns:
        Created example data
    """
    payload = {'tile_id': tile_id, 'question': question}
    if guidelines:
      payload['guidelines'] = guidelines

    return self._post(f'/api/2.0/multi-agent-supervisors/{tile_id}/examples', payload)

  def mas_list_examples(
    self, tile_id: str, page_size: int = 100, page_token: Optional[str] = None
  ) -> 'MultiAgentSupervisorListExamplesResponseDict':
    """List all examples for a Multi-Agent Supervisor.

    Args:
        tile_id: The MAS tile ID
        page_size: Number of examples per page
        page_token: Token for pagination

    Returns:
        Dictionary with structure:
        {
          "examples": [{"example_id", "question", "guidelines": [...], "feedback_records": [...]}, ...],
          "tile_id": "...",
          "next_page_token": "..." (optional)
        }
    """
    params = {'page_size': page_size}
    if page_token:
      params['page_token'] = page_token

    return self._get(f'/api/2.0/multi-agent-supervisors/{tile_id}/examples', params=params)

  def mas_update_example(
    self,
    tile_id: str,
    example_id: str,
    question: Optional[str] = None,
    guidelines: Optional[List[str]] = None,
  ) -> Dict[str, Any]:
    """Update an example in a Multi-Agent Supervisor.

    Args:
        tile_id: The MAS tile ID
        example_id: The example ID to update
        question: Optional new question text
        guidelines: Optional new guidelines

    Returns:
        Updated example data
    """
    payload = {'tile_id': tile_id, 'example_id': example_id}
    if question:
      payload['question'] = question
    if guidelines:
      payload['guidelines'] = guidelines

    return self._patch(f'/api/2.0/multi-agent-supervisors/{tile_id}/examples/{example_id}', payload)

  def mas_delete_example(self, tile_id: str, example_id: str) -> None:
    """Delete an example from the Multi-Agent Supervisor."""
    self._delete(f'/api/2.0/multi-agent-supervisors/{tile_id}/examples/{example_id}')

  def mas_add_examples_batch(
    self, tile_id: str, questions: List[Dict[str, Any]]
  ) -> List[Dict[str, Any]]:
    """Add multiple example questions to the Multi-Agent Supervisor in parallel.

    Args:
        tile_id: The MAS tile ID
        questions: List of dicts with 'question' and optional 'guideline' keys

    Returns:
        List of created examples (None for failed additions)

    Example:
        >>> questions = [
        ...     {"question": "What is X?", "guideline": "Explain clearly"},
        ...     {"question": "How to do Y?", "guideline": "Step by step"}
        ... ]
        >>> manager.mas_add_examples_batch(tile_id, questions)
    """
    created_examples = []

    def create_example_with_progress(question_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
      question_text = question_item.get('question', '')
      guidelines = question_item.get('guideline')
      if guidelines and isinstance(guidelines, str):
        guidelines = [guidelines]

      if not question_text:
        return None

      try:
        example = self.mas_create_example(tile_id, question_text, guidelines)
        logger.info(f'Added MAS example: {question_text[:50]}...')
        return example
      except Exception as e:
        logger.error(f"Failed to add MAS example '{question_text[:50]}...': {e}")
        return None

    # Use ThreadPoolExecutor to run example creation in parallel
    # Limit to 2 concurrent threads to avoid overwhelming the API
    max_workers = min(2, len(questions))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
      # Submit all tasks
      future_to_question = {executor.submit(create_example_with_progress, q): q for q in questions}

      # Collect results
      for future in as_completed(future_to_question):
        result = future.result()
        if result:
          created_examples.append(result)

    return created_examples

  def mas_list_evaluation_runs(
    self, tile_id: str, page_size: int = 100, page_token: Optional[str] = None
  ) -> 'ListEvaluationRunsResponseDict':
    """List all evaluation runs for a Multi-Agent Supervisor.

    Args:
        tile_id: The MAS tile ID
        page_size: Number of evaluation runs per page
        page_token: Token for pagination

    Returns:
        Dictionary with structure:
        {
          "evaluation_runs": [
            {
              "mlflow_run_id": "...",
              "tile_id": "...",
              "name": "...",
              "created_timestamp_ms": 1234567890,
              "last_updated_timestamp_ms": 1234567890
            },
            ...
          ],
          "next_page_token": "..." (optional)
        }
    """
    params = {'page_size': page_size}
    if page_token:
      params['page_token'] = page_token

    return self._get(f'/api/2.0/tiles/{tile_id}/evaluation-runs', params=params)

  # ---------- Low-level HTTP wrappers ----------
  # Using requests directly instead of api_client.do() to avoid automatic retries
  # on quota exhausted errors (RESOURCE_EXHAUSTED). The SDK retries these errors
  # many times (50+ attempts), which wastes time since quota errors won't resolve.
  # Direct requests with 20s timeout allow us to fail fast and provide better error messages.

  def _handle_response_error(self, response: requests.Response, method: str, path: str):
    """Extract detailed error from response and raise with full context."""
    if response.status_code >= 400:
      try:
        error_data = response.json()
        error_msg = error_data.get('message', error_data.get('error', str(error_data)))
        detailed_error = f'{method} {path} failed: {error_msg}'
        logger.error(
          f'API Error: {detailed_error}\nFull response: {json.dumps(error_data, indent=2)}'
        )
        raise Exception(detailed_error)
      except ValueError:
        # Response is not JSON
        error_text = response.text
        detailed_error = f'{method} {path} failed with status {response.status_code}: {error_text}'
        logger.error(f'API Error: {detailed_error}')
        raise Exception(detailed_error)

  def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = self.w.config.authenticate()
    url = f'{self.w.config.host}{path}'
    response = requests.get(url, headers=headers, params=params or {}, timeout=20)
    if response.status_code >= 400:
      self._handle_response_error(response, 'GET', path)
    return response.json()

  def _post(self, path: str, body: Dict[str, Any], timeout: int = 300) -> Dict[str, Any]:
    """POST request with configurable timeout. Default 300s for creation endpoints."""
    headers = self.w.config.authenticate()
    headers['Content-Type'] = 'application/json'
    url = f'{self.w.config.host}{path}'
    response = requests.post(url, headers=headers, json=body, timeout=timeout)
    if response.status_code >= 400:
      self._handle_response_error(response, 'POST', path)
    return response.json()

  def _patch(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    headers = self.w.config.authenticate()
    headers['Content-Type'] = 'application/json'
    url = f'{self.w.config.host}{path}'
    response = requests.patch(url, headers=headers, json=body, timeout=20)
    if response.status_code >= 400:
      self._handle_response_error(response, 'PATCH', path)
    return response.json()

  def _delete(self, path: str) -> Dict[str, Any]:
    headers = self.w.config.authenticate()
    url = f'{self.w.config.host}{path}'
    response = requests.delete(url, headers=headers, timeout=20)
    if response.status_code >= 400:
      self._handle_response_error(response, 'DELETE', path)
    return response.json()


class TileExampleQueue:
  """Background queue for adding examples to tiles (KA/MAS) that aren't ready yet.

  This queue polls tiles every 30 seconds and attempts to add examples once
  the endpoint status is ONLINE.
  """

  def __init__(self):
    self.queue: Dict[str, Tuple[AgentBricksManager, List[Dict[str, Any]], str, float, int]] = {}
    self.lock = threading.Lock()
    self.running = False
    self.thread: Optional[threading.Thread] = None

  def enqueue(
    self,
    tile_id: str,
    manager: AgentBricksManager,
    questions: List[Dict[str, Any]],
    tile_type: str = 'KA',
  ):
    """Add a tile and its questions to the processing queue.

    Args:
        tile_id: The tile ID
        manager: AgentBricksManager instance
        questions: List of question dictionaries
        tile_type: Type of tile ('KA' or 'MAS')
    """
    with self.lock:
      self.queue[tile_id] = (manager, questions, tile_type, time.time(), 0)
      logger.info(
        f'Enqueued {len(questions)} examples for {tile_type} {tile_id} (will add when endpoint is ready)'
      )

    # Start background thread if not running
    if not self.running:
      self.start()

  def start(self):
    """Start the background processing thread."""
    if not self.running:
      self.running = True
      self.thread = threading.Thread(target=self._process_loop, daemon=True)
      self.thread.start()
      logger.info('Started tile example queue background processor')

  def _process_loop(self):
    """Background loop that checks tile status and adds examples when ready.

    Tiles are kept in the queue for up to 1 hour (120 attempts * 30s = 3600s).
    After 1 hour, they are removed with an error log.
    """
    while self.running:
      try:
        # Get snapshot of queue to process
        with self.lock:
          items_to_process = list(self.queue.items())

        # Process each tile
        for tile_id, (
          manager,
          questions,
          tile_type,
          enqueue_time,
          attempt_count,
        ) in items_to_process:
          try:
            # Check if tile has been in queue for more than 1 hour (30 attempts)
            if attempt_count >= 30:
              elapsed_time = time.time() - enqueue_time
              logger.error(
                f'{tile_type} {tile_id} has been in queue for {elapsed_time:.0f}s ({attempt_count} attempts, max 120). '
                f'Removing from queue. Failed to add {len(questions)} examples.'
              )
              with self.lock:
                if tile_id in self.queue:
                  del self.queue[tile_id]
              continue

            # Increment attempt count
            with self.lock:
              if tile_id in self.queue:
                self.queue[tile_id] = (
                  manager,
                  questions,
                  tile_type,
                  enqueue_time,
                  attempt_count + 1,
                )

            # Check endpoint status based on tile type
            if tile_type == 'KA':
              status = manager.ka_get_endpoint_status(tile_id)
            elif tile_type == 'MAS':
              status = manager.mas_get_endpoint_status(tile_id)
            else:
              logger.error(f'Unknown tile type: {tile_type}')
              # Remove from queue
              with self.lock:
                if tile_id in self.queue:
                  del self.queue[tile_id]
              continue

            logger.info(f'{tile_type} {tile_id} status: {status} (attempt {attempt_count + 1}/120)')

            # Only try to add examples if ACTIVE (for KA) or ONLINE (for MAS)
            if status == 'ACTIVE' or status == EndpointStatus.ONLINE.value:
              logger.info(
                f'{tile_type} {tile_id} is ONLINE, attempting to add {len(questions)} examples...'
              )

              # Add examples based on tile type
              if tile_type == 'KA':
                created_examples = manager.ka_add_examples_batch(tile_id, questions)
              elif tile_type == 'MAS':
                created_examples = manager.mas_add_examples_batch(tile_id, questions)

              elapsed_time = time.time() - enqueue_time
              logger.info(
                f'Successfully added {len(created_examples)} examples to {tile_type} {tile_id} '
                f'after {attempt_count + 1} attempts ({elapsed_time:.0f}s)'
              )

              # Remove from queue on success
              with self.lock:
                if tile_id in self.queue:
                  del self.queue[tile_id]
            else:
              logger.info(
                f'{tile_type} {tile_id} not ready yet (status: {status}), will retry in 30s '
                f'(attempt {attempt_count + 1}/120)'
              )

          except Exception as e:
            logger.error(f'Failed to add examples to {tile_type} {tile_id}: {e}', exc_info=True)
            # Remove from queue on failure
            with self.lock:
              if tile_id in self.queue:
                del self.queue[tile_id]

      except Exception as e:
        logger.error(f'Error in tile example queue processor: {e}', exc_info=True)

      # Wait 30 seconds before next iteration
      time.sleep(120)

  def stop(self):
    """Stop the background processing thread."""
    self.running = False
    if self.thread:
      self.thread.join(timeout=5)
    logger.info('Stopped tile example queue background processor')


# Global singleton queue instance
_tile_example_queue: Optional[TileExampleQueue] = None
_queue_lock = threading.Lock()


def get_tile_example_queue() -> TileExampleQueue:
  """Get or create the global tile example queue instance."""
  global _tile_example_queue
  if _tile_example_queue is None:
    with _queue_lock:
      if _tile_example_queue is None:
        _tile_example_queue = TileExampleQueue()
  return _tile_example_queue
